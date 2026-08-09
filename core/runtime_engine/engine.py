"""
SSOT Runtime Engine — production QueryLoop entrypoint.

The active runtime has one execution path:
  request context -> QueryLoop -> audit/result.

QueryLoop owns planning, tool execution, bounded tracking, retry metadata,
and final synthesis. Any stage failure returns structured SSOTRuntimeError
objects — no raw exceptions cross the engine boundary.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass

from typing import Any, Callable

from .audit import AuditLogger
from .budget_controller import BudgetController
from .errors import SSOTRuntimeError, SSOTRuntimeErrorCode, build_error
from .metrics import MetricsCollector
from .models import (
    SSOTRuntimeConfig,
    SSOTRuntimeResult,
    StatelessContext,
    ToolResult,
)
from .query_loop import MAX_VALIDATION_CORRECTION_ROUNDS, QueryLoop, QueryLoopResult
from .runtime_contracts import ExecutionContract
from .stage_events import (
    EXECUTION_COMPLETED,
    RESPONSE_COMPLETED,
    RESPONSE_STARTED,
    HEARTBEAT,
    PLANNER_COMPLETED,
    PLANNER_STARTED,
    TURN_COMPLETED,
    TURN_STARTED,
)
from .tool_runtime import ToolRuntime
from .trace import SpanClock, TraceCollector


class SSOTRuntimeEngine:
    """Single source of truth runtime facade.

    Usage:
        engine = SSOTRuntimeEngine(config, llm_invoke_fn, tool_registry, tool_runtime)
        result = await engine.run(user_input, workspace_id, session_id)
    """

    def __init__(
        self,
        config: SSOTRuntimeConfig | None = None,
        llm_invoke: Callable[..., str] | None = None,
        tool_registry: dict[str, dict[str, Any]] | None = None,
        tool_runtime: ToolRuntime | None = None,
        emitter: Any | None = None,
        approval_handler: Callable[[StatelessContext, dict[str, Any]], Any] | None = None,
        heartbeat_interval_s: float = 1.0,
    ):
        self._config = config or SSOTRuntimeConfig()
        self._llm_invoke = llm_invoke or self._noop_llm
        self._tool_registry = tool_registry or {}
        self._tool_runtime = tool_runtime or ToolRuntime(self._config)
        # Optional emitter — when provided, every stage boundary pushes a
        # tiny status message so the frontend can show progress instead
        # of staring at "思考中…" for 12 seconds on cold-start.
        # Falls back to a no-op so the engine still works in offline tests.
        self._emitter = emitter
        self._approval_handler = approval_handler
        self._heartbeat_interval_s = max(0.5, float(heartbeat_interval_s))
        self._heartbeat_task: asyncio.Task | None = None

        self._audit = AuditLogger()
        self._trace = TraceCollector()

    @property
    def config(self) -> SSOTRuntimeConfig:
        return self._config

    @property
    def tool_runtime(self) -> ToolRuntime:
        return self._tool_runtime

    def register_tool(
        self,
        tool_id: str,
        handler,
        description: str = "",
        args_schema: dict[str, Any] | None = None,
    ) -> None:
        self._tool_registry[tool_id] = {
            "description": description,
            "args_schema": args_schema or {},
        }
        self._tool_runtime.register(tool_id, handler)

    # ========================================================================
    # QUERYLOOP PRODUCTION PIPELINE
    # ========================================================================

    def _emit_stage(self, stage: str, t_start: float, **extra: Any) -> None:
        """Best-effort emit of a stage event through the injected emitter.

        Stages that don't yet have an emitter (offline tests) fall back
        to a fresh ``StreamEmitter()`` instance — the realtime callback
        itself is class-level thread-local, so even a new instance
        pushes through the callback the WebSocket handler already
        registered in the same worker thread.

        We never raise here — emit failures must not block the pipeline.
        """
        if self._emitter is None:
            try:
                from agent.runtime.stream_emitter import StreamEmitter
            except Exception:
                StreamEmitter = None
            if StreamEmitter is None:
                return
            self._emitter = StreamEmitter()
        try:
            elapsed_ms = int((time.monotonic() - t_start) * 1000)
            payload = {
                "stage": stage,
                "elapsed_ms": elapsed_ms,
                **extra,
            }
            self._emitter.emit(stage, payload)
        except Exception:
            pass

    def _start_heartbeat(self, t_total: float) -> None:
        """Launch a periodic heartbeat so the frontend knows SSOT Runtime is alive
        during long LLM/tool phases."""
        if self._emitter is None or self._heartbeat_interval_s <= 0:
            return

        async def _hb():
            try:
                while True:
                    await asyncio.sleep(self._heartbeat_interval_s)
                    if self._emitter is None:
                        return
                    try:
                        elapsed_ms = int((time.monotonic() - t_total) * 1000)
                        self._emitter.emit(HEARTBEAT, {
                            "stage": "alive",
                            "elapsed_ms": elapsed_ms,
                        })
                    except Exception:
                        pass
            except asyncio.CancelledError:
                return

        try:
            loop = asyncio.get_event_loop()
            self._heartbeat_task = loop.create_task(_hb())
        except RuntimeError:
            # No running loop in this context (e.g. called from sync code).
            self._heartbeat_task = None

    async def _stop_heartbeat(self) -> None:
        task = self._heartbeat_task
        self._heartbeat_task = None
        if task is None:
            return
        try:
            task.cancel()
            await asyncio.wait([task], timeout=0.2)
        except Exception:
            pass

    async def run(
        self,
        user_input: str,
        workspace_id: str = "",
        session_id: str = "",
        cwd: str = "",
        extras: dict[str, Any] | None = None,
    ) -> SSOTRuntimeResult:
        """Execute one user request through the canonical QueryLoop pipeline.

        Args:
            extras: caller-supplied metadata map that lands in ``ctx.extras``.
                Approval decisions are bound internally to exact tool-call
                fingerprints and cannot be supplied by callers.
        """
        t_total = time.monotonic()
        metrics = MetricsCollector()
        budget = BudgetController(self._config)
        errors: list[SSOTRuntimeError] = []
        node_results: dict[str, ToolResult] = {}

        try:
            return await self._run_internal(
                user_input, workspace_id, session_id, cwd,
                extras=extras,
                t_total=t_total, metrics=metrics, budget=budget,
                errors=errors, node_results=node_results,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._stop_heartbeat()
            errors.append(build_error(
                "RUNTIME_EXCEPTION",
                f"Engine exception: {str(exc)[:300]}",
                stage="engine",
                risk_level="critical",
            ))
            # 构造最小可用 ctx，避免 _build_result 中 audit 访问 ctx.user_input 失败
            fallback_ctx = StatelessContext(
                workspace_id=workspace_id or "",
                session_id=session_id or "error",
                request_id="error",
                user_input=user_input or "",
                cwd=cwd or "",
                extras={},
            )
            return self._build_result(
                fallback_ctx, {}, "运行时异常，当前请求未完成。请查看系统诊断或稍后重试。", errors, metrics, budget, t_total,
                "critical", False,
            )

    async def _run_internal(
        self,
        user_input: str,
        workspace_id: str,
        session_id: str,
        cwd: str,
        extras: dict[str, Any] | None = None,
        t_total: float = 0.0,
        metrics: MetricsCollector | None = None,
        budget: BudgetController | None = None,
        errors: list | None = None,
        node_results: dict | None = None,
    ) -> SSOTRuntimeResult:
        """Internal run method wrapped by top-level try/except."""
        if metrics is None:
            metrics = MetricsCollector()
        if budget is None:
            budget = BudgetController(self._config)
        if errors is None:
            errors = []
        if node_results is None:
            node_results = {}

        request_span = self._trace.start_request(str(uuid.uuid4())[:8])

        try:
            # P0: announce turn start so frontend logs the request.
            self._emit_stage(TURN_STARTED, t_total,
                             user_input_len=len(user_input or ""))
            # P1: heartbeat the moment we enter — covers all stages.
            self._start_heartbeat(t_total)

            # Stage 1 & 2: Context
            ctx = StatelessContext(
                workspace_id=workspace_id,
                session_id=session_id or f"session_{uuid.uuid4().hex[:12]}",
                request_id=request_span.span.metadata.get("request_id", "unknown"),
                user_input=user_input,
                cwd=cwd,
                extras=dict(extras or {}),
            )

            diag_id = session_id or "none"

            # ── v10: contract boundary — engine_entry check ───────
            from .runtime_contracts import ContractBoundary
            ContractBoundary.validate_all(ctx)

            # ── init result variables before contract validation ──
            errors: list = errors
            node_results: dict = node_results
            final_response = ""

            # ── v4.2: self-healing contract validation ─────────
            from .runtime_contracts import ContractValidator, ContractDegradation
            c_validator = ContractValidator(ExecutionContract)
            contract_report = c_validator.validate_all()

            if contract_report.has_critical_failure():
                errors.append(build_error(
                    "CONTRACT_VIOLATION",
                    f"Critical contract checks failed: "
                    + "; ".join(
                        c.name for c in contract_report.checks
                        if c.level == ContractDegradation.HARD
                    ),
                    stage="engine",
                    risk_level="high",
                ))
                await self._stop_heartbeat()
                return self._build_result(
                    ctx, node_results, final_response,
                    errors, metrics, budget, t_total,
                    risk_level="high", approval_required=False,
                    extra={"contract_report": contract_report},
                )
            ctx.extras["contract_report"] = contract_report

            risk_level = "low"
            approval_required = False

            conv_history_block = ctx.extras.get("conversation_history_block") or ""
            task_intent = detect_task_intent(user_input)

            clarification = build_operational_clarification(ctx.user_input, task_intent)
            if clarification:
                ctx.extras["operational_clarification"] = clarification

            # ── QueryLoop: the only tool-capable execution path ──────────────
            # The loop owns planner LLM calls, tool execution, bounded tracking,
            # retry metadata, and final synthesis. This keeps active runtime
            # state in one place instead of splitting it across parallel planners.
            self._emit_stage(PLANNER_STARTED, t_total)

            query_loop = QueryLoop(
                self._config, self._tool_registry,
                self._tool_runtime,
                llm_invoke=self._llm_invoke,
                emitter=self._emitter,
                approval_handler=self._approval_handler,
            )
            loop_result = await query_loop.run(ctx, budget, metrics)

            self._emit_stage(PLANNER_COMPLETED, t_total,
                             iterations=loop_result.iterations)
            self._emit_stage(EXECUTION_COMPLETED, t_total,
                             tool_calls=loop_result.total_tool_calls)

            for r in loop_result.tool_results:
                node_results[r.call_id] = ToolResult(
                        node_id=r.call_id,
                        tool=r.tool_name,
                        success=r.ok,
                        data=r.output,
                        error=r.error,
                        latency_ms=float(r.latency_ms or 0.0),
                )

            final_response = loop_result.final_response
            risk_level = loop_result.risk_level or "low"
            approval_required = bool(loop_result.approval_required)
            if loop_result.error and loop_result.error not in {
                    "approval_required",
                    "duplicate_successful_tool_call",
                    "duplicate_tool_call",
            }:
                first_loop_error = loop_result.errors[0] if loop_result.errors else loop_result.error
                loop_error_code = self._resolve_loop_error_code(
                        loop_result.error, first_loop_error, loop_result.hard_block
                )
                errors.append(build_error(
                        loop_error_code,
                        first_loop_error,
                        stage="query_loop",
                        risk_level=risk_level,
                ))
            metrics.set_llm_calls(loop_result.llm_calls)
            metrics.capture_query_loop_execution(
                    loop_result.metrics.get("execution_duration_ms", 0.0),
                    node_results,
                    loop_result.metrics.get("max_parallel_width", 0),
            )
            await self._stop_heartbeat()

            return self._build_result(
                    ctx, node_results, final_response,
                    errors, metrics, budget, t_total,
                    risk_level, approval_required,
                    extra={
                        "query_loop": True,
                        "iterations": loop_result.iterations,
                        "tool_calls": loop_result.total_tool_calls,
                        "llm_calls": loop_result.llm_calls,
                        "used_tools": loop_result.total_tool_calls > 0,
                        "conversation_ref": bool(conv_history_block),
                        "conversation_history_used": bool(conv_history_block),
                        "approval_required": approval_required,
                        "approval_nodes": loop_result.approval_nodes,
                        "approval_details": loop_result.approval_details,
                        "hard_block": bool(loop_result.hard_block),
                        **loop_result.metrics,
                    },
            )
        finally:
            request_span.stop()


    # ========================================================================
    # Result assembly
    # ========================================================================

    def _build_result(
        self,
        ctx: StatelessContext,
        node_results: dict[str, ToolResult],
        final_response: str,
        errors: list[SSOTRuntimeError],
        metrics: MetricsCollector,
        budget: BudgetController,
        t_total: float,
        risk_level: str,
        approval_required: bool,
        extra: dict[str, Any] | None = None,
    ) -> SSOTRuntimeResult:
        total_ms = (time.monotonic() - t_total) * 1000
        metrics.capture_total(total_ms)
        self._audit.create_record(
            ctx, node_results,
            risk_level=risk_level,
            approval_required=approval_required,
            llm_call_count=budget.llm_calls,
            duration_ms=total_ms,
        )

        m = metrics.snapshot()

        base_meta = {
            "route": "",
            "planner_skipped": False,
            "used_tools": len(node_results) > 0,
            "tool_calls": len(node_results),
            # v3.12: approval tracking
            "approval_required": False,
            "hard_block": False,
            "approval_reason": "",
            "approval_nodes": [],
            "approval_details": [],
            "command_summary": [],
            "tool_summary": [],
            # v3.13: conversation context
            "conversation_ref": False,
            "conversation_history_used": False,
            "context_budget": dict(ctx.extras.get("runtime_context_budget") or {}),
            "context_compacted": False,
            "context_estimated_tokens": 0,
            "output_truncated": bool(ctx.extras.get("output_truncated", False)),
            "output_truncation_reason": str(ctx.extras.get("output_truncation_reason", "")),
        }
        if extra:
            base_meta.update(extra)

        return SSOTRuntimeResult(
            request_id=ctx.request_id,
            # A natural-language explanation cannot make the run successful
            # when every requested tool operation failed.
            success=(
                len(errors) == 0
                and not (bool(node_results) and not any(r.success for r in node_results.values()))
            ),
            total_latency_ms=total_ms,
            planner_latency_ms=m.planner_duration_ms,
            execution_latency_ms=m.execution_duration_ms,
            merge_latency_ms=0.0,
            response_latency_ms=m.response_duration_ms,
            max_layer_latency_ms=0.0,
            node_results=node_results,
            final_response=final_response,
            errors=[e.message for e in errors],
            metadata={
                **base_meta,
                "workspace_id": ctx.workspace_id,
                "session_id": ctx.session_id,
                "node_success_count": sum(1 for r in node_results.values() if r.success),
                "node_failure_count": sum(1 for r in node_results.values() if not r.success),
                "all_nodes_success": all(r.success for r in node_results.values()) if node_results else True,
                "risk_level": risk_level,
                "approval_required": approval_required,
                "llm_calls": budget.llm_calls,
                "structured_errors": [e.to_dict() for e in errors],
                "metrics": metrics.to_dict(),
                "alias_normalizations": ctx.extras.get("alias_normalizations", []),
                "plan_enrichment_events": ctx.extras.get("plan_enrichment_events", []),
                "pre_exec_repair_events": ctx.extras.get("pre_exec_repair_events", []),
                "pre_exec_repair_applied": ctx.extras.get("pre_exec_repair_applied", False),
                # v3.10 (tool retry): aggregate per-node retry decisions
                # collected by stage 10. ``retry_summary`` is a small
                # dict (counts); ``retry_events`` is the full list of
                # ``tool_retry`` events for audit.
                "retry_summary": ctx.extras.get("retry_summary", {
                    "retry_attempts": 0,
                    "retried_nodes": [],
                    "retry_succeeded": 0,
                    "retry_failed": 0,
                    "retry_blocked": 0,
                }),
                "retry_events": ctx.extras.get("retry_events", []),
                "validation_correction_summary": {
                    "attempts": len(ctx.extras.get("validation_correction_events", [])),
                    "max_attempts": MAX_VALIDATION_CORRECTION_ROUNDS,
                    "exhausted": bool(ctx.extras.get("validation_correction_exhausted", False)),
                },
                "validation_correction_events": ctx.extras.get("validation_correction_events", []),
                "tool_recovery_events": ctx.extras.get("tool_recovery_events", []),
                "tracking_summary": ctx.extras.get("tracking_summary", {}),
                "tracking_events": ctx.extras.get("tracking_events", []),
                "approval_events": ctx.extras.get("approval_events", []),
                "approval_resolved": ctx.extras.get("approval_resolved", False),
                "output_truncated": bool(
                    base_meta.get("output_truncated") or ctx.extras.get("output_truncated", False)
                ),
                "output_truncation_reason": str(
                    base_meta.get("output_truncation_reason")
                    or ctx.extras.get("output_truncation_reason", "")
                ),
            },
        )

    def _noop_llm(self, **kwargs) -> str:
        return '{"nodes": []}'

    @staticmethod
    def _resolve_loop_error_code(error_key: str, first_error: str, hard_block: bool) -> str:
        """Map QueryLoop error keys to canonical SSOTRuntimeErrorCode values."""
        from .errors import SSOTRuntimeErrorCode

        # Semantic validation: code is embedded in the error text
        if error_key == "semantic_validation_failed" and isinstance(first_error, str):
            parts = first_error.split(":", 2)
            if len(parts) >= 3:
                return parts[1]

        # Budget exhaustion
        if error_key in ("budget_exceeded",):
            return SSOTRuntimeErrorCode.BUDGET_LLM_EXCEEDED

        # Max iterations / timeout
        if error_key in ("max_iterations",):
            return SSOTRuntimeErrorCode.BUDGET_TIME_EXCEEDED

        # No response from LLM
        if error_key in ("no_response",):
            return SSOTRuntimeErrorCode.PLANNER_TIMEOUT

        # Doom-loop (repeated failing tool calls)
        if error_key and error_key.startswith("doom_loop"):
            return SSOTRuntimeErrorCode.EXECUTION_TOOL_EXCEPTION

        # Hard block from risk policy
        if hard_block:
            return SSOTRuntimeErrorCode.RISK_CRITICAL_DENIED

        # Default fallback — keep as a structured code rather than a raw string
        return SSOTRuntimeErrorCode.VALIDATION_UNSAFE_OPERATION


# ── v3.14: Task-intent detection (P3-8: constants defined at file bottom-half, scattered) ──
#
_TASK_INTENT_VERBS = (
    # Explicit action verbs
    "读取", "分析", "检查", "生成",
    "总结", "排查", "对比", "诊断", "判断",
    "监测", "追踪", "绘制", "统计", "汇报",
    "评估", "审查", "核实", "校验", "整理",
    "执行", "处理", "导出", "保存",
    "执行",
    "查找", "寻找", "搜索", "确认",
    "跟踪", "持续", "监听", "等待",
    "扫", "检测",
    # Visual/file reference patterns
    "看这个文件", "看这个截图", "看这个日志", "看这个数据",
    "看看这个", "看下这个", "查看这个",
    # Task-completion patterns
    "帮我看看", "帮我看下", "分析一下",
    "给出结论", "给出原因", "处理建议", "建议怎么",
    # Report patterns
    "生成报告", "导出结果", "保存分析",
)

# Patterns that make a definition question NOT task intent.
_Q_DEFINITION_PATTERNS = ("是什么", "什么是", "什么叫", "的定义", "介绍一下")

# Patterns that make a "是什么"-containing query STILL task intent.
_Q_TASK_OVERRIDES = (
    "帮我分析", "分析一下", "看这个", "看看这个",
    "这个截图", "这个日志", "这个文件", "这个数据",
    "读取", "检查一下", "排查", "是什么原因", "是什么问题",
    "为什么会这样", "什么异常", "什么错误",
    "查找", "跟踪", "发起",
)

_TASK_INTENT_RESULT_FIELDS = ("结论", "发现", "原因", "建议", "异常",
                              "正常", "风险", "下一步", "依据", "诊断")

_COMMAND_GOAL_HINTS = (
    "查看", "查询", "获取", "检查", "确认", "采集", "诊断",
    "ip", "IP", "地址", "内核", "版本", "状态", "接口",
    "CPU", "cpu", "内存", "磁盘", "路由", "邻居",
)

_COMMAND_LITERAL_HINTS = (
    "`", "\n", "uname", "display", "dis ", "show", "ping",
    "traceroute", "trace ", "df ", "free ", "ip ", "ifconfig",
    "cat ", "ls ", "pwd", "systemctl", "curl", "netstat", "ss ",
)

_LOGIN_HINTS = ("登录", "连接", "进入", "ssh", "SSH", "telnet", "Telnet")


@dataclass
class TaskIntentResult:
    """Structured task-intent detection result."""
    is_task: bool = False
    intent_type: str = ""       # analysis / file_read_analysis / ...
    evidence: list[str] = None
    requires_tool_likely: bool = False

    def __post_init__(self):
        if self.evidence is None:
            self.evidence = []

    @property
    def requires_execution(self) -> bool:
        """v4 contract alias: the user request requires the
        runtime to produce real tool execution.

        Returns False for ``conversational_followup`` even when
        ``requires_tool_likely`` is True — a meta-question about
        past behaviour (e.g. "你上轮为什么不总结") should never
        trigger the execution-obligation guard.
        """
        if self.intent_type == "conversational_followup":
            return False
        return bool(self.requires_tool_likely)


def _has_target_hint(text: str) -> bool:
    if any(ch.isdigit() for ch in text) and any(sep in text for sep in (".", "_", "-", "号")):
        return True
    return any(k in text for k in ("服务器", "交换机", "路由器", "防火墙", "资产", "设备"))


def build_operational_clarification(
    user_input: str,
    intent: TaskIntentResult | None = None,
) -> dict[str, Any] | None:
    """Return model-visible guidance for ambiguous login/command requests.

    This must not answer before QueryLoop. It only supplies a scoped hint so the
    LLM can decide whether to ask for missing details or use safe tools.
    """
    text = (user_input or "").strip()
    if not text:
        return None
    lower = text.lower()
    has_login_intent = any(k in text for k in _LOGIN_HINTS) or "ssh" in lower or "telnet" in lower
    has_command_literal = any(k in text for k in _COMMAND_LITERAL_HINTS)
    has_goal_hint = any(k in text for k in _COMMAND_GOAL_HINTS)
    intent = intent or detect_task_intent(text)
    if intent.intent_type != "command_check" and not has_login_intent and not has_command_literal:
        return None

    has_target = _has_target_hint(text)

    missing: list[str] = []
    if has_login_intent and not has_target:
        missing.append("目标设备")
    if not has_command_literal and not has_goal_hint:
        missing.append("要执行的命令或检查目标")

    if not missing:
        return None

    guidance = (
        "The current request looks operational but may be underspecified. "
        f"Potentially missing fields: {'、'.join(missing)}. "
        "Do not invent targets, credentials, commands, or device state. "
        "If safe progress is impossible without these fields, ask a concise "
        "clarifying question. If safe read-only discovery is available, the LLM "
        "may choose tools normally."
    )
    return {"missing": missing, "guidance": guidance}


# Meta-questions about past behaviour — these are conversational
# followups, NOT new tool tasks. The v4
# ``EXECUTION_OBLIGATION_ENFORCED`` guard must NOT fire for
# them, even when the question text contains a task verb
# (e.g. "你上轮为什么不总结" matches "总结" but is a meta-
# question about a past action, not a request to summarise
# again).
_META_QUESTION_VERBS = (
    "为什么", "怎么", "为何", "怎么会", "是不是", "对吗",
    "什么情况", "什么意思", "说啥", "说了啥",
)
_PAST_REFERENCES = (
    "上轮", "上一轮", "上次的", "上次", "刚才", "之前",
    "刚才的", "之前的", "上轮的", "刚才你", "你刚才",
    "你上轮", "你上一轮", "你刚才的", "你上次的",
    "上一轮的", "前一轮", "前一次", "前一",
)


def detect_task_intent(user_input: str) -> TaskIntentResult:
    """Unified task-intent detector.

    Returns a structured TaskIntentResult with:
      - is_task: whether this is a task-type request
      - intent_type: classification (analysis, file_read_analysis, etc.)
      - evidence: which rules matched
      - requires_tool_likely: whether tools are probably needed

    Rules (in priority order):
      0. Meta-question about past behaviour ("你上轮为什么
         不总结", "刚才怎么没分析") → NOT task, classified
         as ``conversational_followup``.
      1. Definition questions ("是什么", "什么是") → NOT task
         UNLESS the query also contains task-override patterns.
      2. Task verbs → task intent.
      3. Visual/file reference → task intent.
      4. Report/generation → task intent.
      5. Otherwise → not task intent.
    """
    text = (user_input or "").strip()
    result = TaskIntentResult()
    if not text:
        return result

    # Step 0: meta-question about past behaviour. We require
    # BOTH a meta-question verb AND a past reference — a single
    # signal is not enough. "为什么" alone is too noisy (it
    # also appears in "这个截图为什么这样", which IS a task);
    # "上轮" alone is also too noisy ("上次的报告呢" is a
    # task, not a followup).
    has_meta = any(p in text for p in _META_QUESTION_VERBS)
    has_past = any(p in text for p in _PAST_REFERENCES)
    if has_meta and has_past:
        result.intent_type = "conversational_followup"
        result.is_task = False
        result.requires_tool_likely = False
        result.evidence = ["conversational_followup"]
        return result

    # Step 1: Check for definition patterns
    has_def = any(p in text for p in _Q_DEFINITION_PATTERNS)
    has_override = any(p in text for p in _Q_TASK_OVERRIDES)

    if has_def and not has_override:
        return result  # Pure definition → not task

    # Step 2: Check for task verbs and patterns
    matched = [v for v in _TASK_INTENT_VERBS if v in text]
    if matched:
        result.is_task = True
        result.evidence = matched
        result.requires_tool_likely = True

        # Classify intent type
        text_lower = text.lower()
        if any(w in text_lower for w in ("文件", "file", "read", "读取", "日志", "log")):
            result.intent_type = "file_read_analysis"
        elif any(w in text_lower for w in ("命令", "执行", "exec", "show", "ping")):
            result.intent_type = "command_check"
        elif any(w in text_lower for w in ("诊断", "排查", "问题", "异常", "故障", "起不来")):
            result.intent_type = "analysis"
        elif any(w in text_lower for w in ("报告", "report", "导出")):
            result.intent_type = "report"
        elif any(w in text_lower for w in ("分析", "总结", "判断")):
            result.intent_type = "analysis"
        else:
            result.intent_type = "analysis"

        return result

    # Step 3: Check for "截图"/"为什么这样" patterns without explicit verbs
    if any(p in text for p in ("为什么", "这个截图", "为什么会", "看看有问题")):
        result.is_task = True
        result.evidence = ["contextual_inquiry"]
        result.intent_type = "analysis"
        result.requires_tool_likely = True

    return result

# ── v3.15: Final-response validator ───────────────────────────────────────

_TASK_ANALYSIS_FIELDS = (
    "结论", "发现", "原因", "建议", "异常", "正常",
    "风险", "下一步", "依据", "诊断",
)

_TASK_BUSINESS_RESULT_FIELDS = (
    "状态", "数量", "报告链接", "stdout", "stderr",
    "超时", "成功数", "失败数", "检查结果", "分析结论",
    "文件", "制品", "数据", "来源", "引用",
)

_TASK_BOGUS_RESPONSE_PATTERNS = (
    "工具执行成功", "结果已返回",
    "文件已读取，可以继续",
    "已完成本次操作",
    "数据已获取，未发现更多",
    "收到",
    "已完成",
    "No tools were executed",
    "readartifact completed",
    "readartifact succeeded",
    "Completed",
)


@dataclass
class FinalResponseValidatorResult:
    valid: bool = True
    reason: str = ""
    matched_placeholder: str = ""
    should_retry_response: bool = False
    has_analysis_fields: bool = False
    has_business_result: bool = False
    has_explicit_failure_reason: bool = False
    placeholder_like: bool = False


def validate_final_response(
    user_input: str,
    final_response: str,
) -> FinalResponseValidatorResult:
    """Validate that the final_response actually completes the user's task."""
    rr = FinalResponseValidatorResult()

    task = detect_task_intent(user_input)
    if not task.is_task:
        return rr

    response = (final_response or "").strip()
    if not response:
        rr.valid = False
        rr.reason = "empty response for task request"
        rr.should_retry_response = True
        return rr

    # ── Step 1: check analysis / business-result fields ───────────
    rr.has_analysis_fields = any(f in response for f in _TASK_ANALYSIS_FIELDS)
    rr.has_business_result = any(f in response for f in _TASK_BUSINESS_RESULT_FIELDS)

    # Explicit failure reason ("缺少"/"无法"/"不足" + 分析意图)
    rr.has_explicit_failure_reason = (
        any(w in response for w in ("缺少", "无法完成", "不足", "没有可分析", "未返回"))
        and ("分析" in response or "内容" in response or "数据" in response)
    )

    if rr.has_analysis_fields or rr.has_business_result or rr.has_explicit_failure_reason:
        return rr  # valid

    # ── Step 2: detect placeholder-like patterns ──────────────────
    rr.placeholder_like = any(p in response for p in _TASK_BOGUS_RESPONSE_PATTERNS)
    if rr.placeholder_like:
        rr.valid = False
        rr.reason = f"placeholder-like response"
        rr.matched_placeholder = "bogus_pattern"
        rr.should_retry_response = True
        return rr

    # ── Step 3: very short response without analysis → invalid ────
    if len(response) < 30:
        rr.valid = False
        rr.reason = "very short response without analysis fields"
        rr.should_retry_response = True
        return rr

    # ── Step 4: longer but still looks like no analysis completed ─
    # (e.g. "我已经读取了文件，工具执行成功，数据已返回...")
    _no_analysis_words = ("可以继续", "已获取", "已返回", "已读取", "请稍后")
    if any(w in response for w in _no_analysis_words):
        rr.valid = False
        rr.reason = "response mentions tool success but no analysis"
        rr.should_retry_response = True
        return rr

    return rr


def plan_nodes_empty_for_task(user_input: str) -> bool:
    return detect_task_intent(user_input).is_task


def _is_task_incomplete_final_response(user_input: str, final_response: str) -> bool:
    vr = validate_final_response(user_input, final_response)
    return not vr.valid
