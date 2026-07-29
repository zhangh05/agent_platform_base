# prompts/policy.py
"""Prompt input/output policy — blocks unsafe content, detects injection, fake refs."""

import re
from dataclasses import dataclass, field

FORBIDDEN_INPUT_PATTERNS = [
    (r'(password|passwd)\s+\S+', "password"),
    (r'(secret\s+\S+)', "secret"),
    (r'sk-[A-Za-z0-9]{16,}', "api_key"),
    (r'token[=:]\s*\S{8,}', "token"),
    (r'community\s+\S+', "community"),
    (r'-----BEGIN\s.*PRIVATE\sKEY-----', "private_key"),
    (r'Bearer\s+\S{8,}', "authorization"),
    (r'(source_raw|raw_source|full_source)', "raw_source"),
    (r'(/[A-Za-z0-9._-]+){3,}', "absolute_path"),
    (r'raw_prompt|full_prompt', "raw_prompt"),
]

FORBIDDEN_OUTPUT_PATTERNS = [
    (r'我已执行|我已经执行|已部署|已修改生产环境|production-ready', "unverified_execution_claim"),
    (r'无需人工复核|不用人工复核|no manual review needed', "hide_review"),
    (r'(password|passwd|community|token|api_key)\s*[=:]\s*\S+', "secret_output"),
]

INJECTION_PATTERNS = [
    (r'忽略以上规则|忽略以上指令|ignore (previous|above) (instructions?|rules?)', "ignore_rules"),
    (r'关闭安全策略|disable safety|turn off safety', "disable_safety"),
    (r'不要遵守.*规则|do not follow.*rules', "disobey_rules"),
    (r'隐藏人工复核|hide manual review|不要显示.*复核', "hide_manual_review"),
    (r'伪造.*成功|fake.*success|假装.*完成', "fake_success"),
    (r'告诉我.*key|告诉我.*密码|告诉我.*token|show me.*key', "key_request"),
]

FAKE_REF_PATTERN = re.compile(r'\b(?:art|job|run|report|trace)[a-zA-Z0-9_-]{8,64}\b')


@dataclass
class PolicyResult:
    ok: bool = True
    issues: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    injection_detected: bool = False

    def as_dict(self):
        return {"ok": self.ok, "issues": [i.get("rule","") for i in self.issues],
                "warnings": self.warnings, "injection_detected": self.injection_detected}


def check_prompt_input(prompt_spec, safe_context: dict) -> PolicyResult:
    """Check that safe_context does not contain forbidden content."""
    result = PolicyResult()
    ctx_str = str(safe_context).lower()
    for pattern, rule in FORBIDDEN_INPUT_PATTERNS:
        if re.search(pattern, ctx_str, re.IGNORECASE):
            result.ok = False
            result.issues.append({"rule": rule, "pattern": pattern[:40]})
    return result


def check_prompt_text(text: str, prompt_spec=None) -> PolicyResult:
    """Check rendered prompt text for forbidden patterns."""
    result = PolicyResult()
    for pattern, rule in FORBIDDEN_INPUT_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            result.ok = False
            result.issues.append({"rule": rule, "pattern": pattern[:40]})
    return result


# ── Negation context detection ──
# Chinese negation characters & words that may precede a forbidden phrase
# NOTE: single-char "无" removed — too ambiguous (appears in dangerous phrases like "无需人工复核")
_CN_NEGATION_WORDS = [
    "不", "没", "非", "勿", "别", "莫", "未",
    "不会", "不可", "绝不", "并非", "不是", "从未",
    "禁止", "否定", "绝不",
]
_EN_NEGATION_WORDS = ["not", "never", "don't", "doesn't", "won't", "can't", "cannot"]


def _is_negation_context(text: str, match_start: int) -> bool:
    """Check if a forbidden-pattern match exists within a negation phrase.
    
    Scans a window of up to 20 characters before the match for negation
    words that would make the flagged text part of a boundary disclaimer
    (e.g. "我不会声称'已修改生产环境'" — the LLM is stating it will not claim execution).
    """
    window_start = max(0, match_start - 20)
    before = text[window_start:match_start].lower()
    for word in _CN_NEGATION_WORDS:
        # Check if negation word is in the window and actually precedes the match
        idx = before.rfind(word)
        if idx >= 0:
            # Require the negation to be reasonably close (not from a previous sentence)
            chars_between = len(before) - idx - len(word)
            if chars_between <= 12:  # negation word within ~12 chars of the match
                return True
    for word in _EN_NEGATION_WORDS:
        idx = before.rfind(word)
        if idx >= 0 and len(before) - idx - len(word) <= 8:
            return True
    return False


def check_prompt_output(prompt_spec, llm_output: str, citations: list = None) -> PolicyResult:
    """Check LLM output for forbidden content and fake references."""
    result = PolicyResult()
    text = str(llm_output).lower()
    citations = citations or []

    for pattern, rule in FORBIDDEN_OUTPUT_PATTERNS:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            if not _is_negation_context(llm_output, m.start()):
                result.ok = False
                result.issues.append({"rule": rule, "pattern": pattern[:40],
                                      "matched": m.group()[:80]})

    # Fake reference detection
    cite_ids = {c.get("citation_id", "") for c in citations}
    fake_refs = FAKE_REF_PATTERN.findall(llm_output)
    known = set()
    for c in citations:
        known.add(c.get("source_id", ""))
    for ref in fake_refs[:10]:
        if ref not in known and ref not in cite_ids:
            result.ok = False
            result.issues.append({"rule": "fake_reference", "ref": ref})

    return result


def detect_prompt_injection(user_input: str) -> PolicyResult:
    """Detect prompt injection attempts."""
    result = PolicyResult()
    text = str(user_input).lower()
    for pattern, rule in INJECTION_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            result.injection_detected = True
            result.warnings.append(rule)
    return result
