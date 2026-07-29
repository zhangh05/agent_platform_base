"""Fast-path classifier — narrow-rule direct-answer routing.

Purpose: skip the full SSOT Runtime planner/compiler/execution pipeline for
queries that clearly do not need tools (greetings, definition questions,
translation, summarisation).  The classifier is intentionally *narrow*:
only patterns that are unambiguous get the fast path; everything else
falls through to full SSOT Runtime.

Design principle: when in doubt, fall through — a false positive
(blocking a real tool need) is worse than a false negative (sending
a simple question to the planner).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FastPathDecision:
    enabled: bool
    route: str          # "greeting" | "simple_question" | "direct_answer" | ""
    reason: str


# ── Whitelist: patterns that enable fast path ──────────────────────

_GREETING_PATTERNS = (
    "你好", "hello", "hi ", "在吗", "你是谁", "你是",
    "hello!", "hi!",
)

_SIMPLE_QUESTION_PATTERNS = (
    "翻译", "润色", "改写", "总结这段", "总结一下", "概括",
    "解释一下", "是什么", "什么是", "什么叫", "的定义",
    "介绍一下", "了解", "区别",
)


# ── Hard tool keywords: always force full SSOT Runtime ────────────────────
# Any match here means the user wants the system to DO something
# (read a file, run a command, check a device, etc.), so fast-path
# is categorically wrong.

_HARD_TOOL_KEYWORDS = (
    "读取", "文件", "执行", "命令", "删除", "修改",
    "提交", "推送", "保存", "写入", "部署",
    "查看", "检查", "排查",
    "ping", "telnet", "traceroute", "ssh",
    "README", "readme", ".md", ".py", ".yaml", ".json",
    "workspace/", "目录",
    "snmp", "netconf",
)


# ── Network-domain keywords (protocols, devices, concepts) ────────
# Alone these do NOT block fast path (e.g. "NAT 是什么" is fine).
# Combined with troubleshooting keywords they DO block.

_NETWORK_KEYWORDS = (
    "OSPF", "BGP", "ISIS", "SRv6", "MTU", "NAT", "VLAN",
    "接口", "交换机", "防火墙", "路由", "策略",
    "光模块", "ACL", "MPLS", "VRRP", "STP",
    "IP", "TCP", "UDP", "DNS", "DHCP",
)


# ── Troubleshooting-intent keywords ───────────────────────────────
# These signal the user has a *problem* and needs diagnosis, not
# a textbook definition.  Combined with a network keyword they
# force full SSOT Runtime.

_TROUBLESHOOTING_KEYWORDS = (
    "不通", "起不来", "不生效", "失败", "异常", "故障",
    "很慢", "访问慢", "丢包", "超时", "flap", "down",
    "红灯", "告警", "原因", "怎么排查", "帮我分析",
    "怎么解决", "如何处理", "排查", "诊断",
    "不匹配", "不一致",
)


# ── Conversation-ref patterns ────────────────────────────────────
# These reference a previous conversation turn. When detected AND
# conversation_history is available, the system must answer from
# history rather than claiming ignorance.

_CONVERSATION_REF_PATTERNS = (
    # Patterns that literally ask to RECALL previous conversation content:
    # "what did I just say", "what did you just say", etc.
    # Comprehension follow-ups like "什么意思" are handled separately below:
    # they should use recent conversation, but not trigger tools.
    "我上句话说了什么", "上句是什么", "刚才我说了什么",
    "你还记得我刚才说什么吗",
    "你说了什么还记得吗",
    "你刚才说什么了", "我刚说了什么",
    "我说了什么", "我说的什么",
)

_CONVERSATION_COMPREHENSION_PATTERNS = (
    "什么意思", "什么含义", "啥意思", "这是什么意思",
    "这句话什么意思", "你刚才是什么意思",
    "什么情况", "怎么了", "怎么回事",
)


def is_conversation_ref(user_input: str) -> bool:
    """Check if the input references a previous conversation turn.

    Used by the SSOT Runtime engine to detect follow-up queries that need
    conversation_history injected.  Detected refs never route through
    memory search — they are answered directly from session.history.
    """
    text = (user_input or "").strip()
    for pat in _CONVERSATION_REF_PATTERNS:
        if pat in text:
            return True
    return False


def is_conversation_comprehension_ref(user_input: str) -> bool:
    """Return True for short follow-ups asking to explain the prior answer.

    These are not recall questions and should not be answered by repeating
    history. They are still conversation-scoped, so the engine should route
    them to the direct-answer LLM with history injected and no tools.
    """
    text = (user_input or "").strip()
    if not text or len(text) > 30:
        return False
    # Single punctuation or very short tokens ("？", "?", "嗯", "哦")
    # are conversation follow-ups when they arrive after prior messages.
    if len(text) <= 2:
        return True
    return any(pat in text for pat in _CONVERSATION_COMPREHENSION_PATTERNS)


def _build_conversation_history_block(history: list[dict[str, str]]) -> str:
    """Format conversation_history entries into a prompt-ready block."""
    if not history:
        return ""
    lines = ["RECENT CONVERSATION HISTORY:"]
    for i, entry in enumerate(history, 1):
        role = entry.get("role", "unknown")
        content = entry.get("content", "")
        lines.append(f"  [{i}] {role}: {content}")
    return "\n".join(lines)


# ── Helper ────────────────────────────────────────────────────────

def _has_any(text: str, keywords: tuple[str, ...]) -> bool:
    """Returns True if any keyword appears as a substring in *text*."""
    lower = text.lower()
    for kw in keywords:
        if kw.lower() in lower:
            return True
    return False


# ── Classifier ─────────────────────────────────────────────────────

def classify_direct_answer(user_input: str) -> FastPathDecision:
    """Narrow-rule classifier: should this input skip the planner?

    Returns:
        FastPathDecision with ``enabled=True`` only for clear-cut
        non-tool scenarios (greetings, definition questions, etc.).

    Decision tree:
      1. Empty → full SSOT Runtime
      2. Greeting (short) → fast path
      3. Simple-question whitelist matched?
         a. Hard-tool keyword in input → full SSOT Runtime
         b. Network + troubleshooting combo → full SSOT Runtime
         c. Otherwise → fast path
      4. No whitelist match → full SSOT Runtime

    This ordering prevents:
      - "解释一下 OSPF 邻居起不来的原因"  → fast-path blocked (network+troubleshooting)
      - "帮我分析防火墙策略不生效"        → fast-path blocked (troubleshooting)
      - "NAT 是什么 / OSPF 是什么"        → fast path (pure definition)
    """
    text = (user_input or "").strip()
    if not text:
        return FastPathDecision(enabled=False, route="", reason="empty input")

    # 1.  Greeting patterns (very short, no ambiguity).
    for pat in _GREETING_PATTERNS:
        if text.lower().startswith(pat.lower()) and len(text) <= 10:
            return FastPathDecision(
                enabled=True, route="greeting",
                reason=f"greeting pattern: {pat}",
            )

    # 2.  Does the input match a simple-question whitelist pattern?
    simple_question_matched = False
    matched_pat = ""
    for pat in _SIMPLE_QUESTION_PATTERNS:
        if pat in text:
            simple_question_matched = True
            matched_pat = pat
            break

    if not simple_question_matched:
        # No whitelist pattern → fall through to full SSOT Runtime.
        return FastPathDecision(
            enabled=False, route="",
            reason="no fast-path pattern matched",
        )

    # 3a. Hard-tool keyword block.  The user asked "翻译这个文件"
    #     or "检查一下" — this requires actual tool execution.
    if _has_any(text, _HARD_TOOL_KEYWORDS):
        return FastPathDecision(
            enabled=False, route="",
            reason="hard_tool_keyword",
        )

    # 3b. Network + troubleshooting combo block.  The user asked
    #     "解释一下 OSPF 邻居起不来的原因" — they have a real
    #     network problem that needs diagnosis, not a textbook
    #     definition.
    has_network = _has_any(text, _NETWORK_KEYWORDS)
    has_troubleshoot = _has_any(text, _TROUBLESHOOTING_KEYWORDS)
    if has_network and has_troubleshoot:
        return FastPathDecision(
            enabled=False, route="",
            reason="network_troubleshooting_combo",
        )

    # 3c. Pure simple question (definition, translation, etc.)
    #     with no tool/troubleshooting intent → fast path.
    return FastPathDecision(
        enabled=True, route="simple_question",
        reason=f"simple question pattern: {matched_pat}",
    )
