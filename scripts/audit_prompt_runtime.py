#!/usr/bin/env python3
"""Prompt Runtime Audit."""
import json, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTS = os.path.join(ROOT, "reports")
os.makedirs(REPORTS, exist_ok=True)

critical, high, warnings = [], [], []

def run():
    # Check prompt runtime modules
    for mod in ["prompts/schemas.py", "prompts/loader.py", "prompts/renderer.py", "prompts/policy.py", "prompts/registry.yaml"]:
        if os.path.exists(os.path.join(ROOT, mod)):
            warnings.append(f"OK: {mod}")
        else:
            critical.append(f"Missing: {mod}")

    # Check templates
    tmpl_dir = os.path.join(ROOT, "prompts", "templates")
    if os.path.exists(tmpl_dir):
        for t in ["response_compose.md", "context_qa.md", "manual_review_explain.md", "result_summarize.md", "job_failure_explain.md", "report_summary.md", "artifact_summary_explain.md"]:
            if os.path.exists(os.path.join(tmpl_dir, t)):
                warnings.append(f"OK: templates/{t}")
            else:
                high.append(f"Missing template: {t}")

    # Check safe_generate uses prompt runtime
    runtime_path = os.path.join(ROOT, "agent", "llm", "runtime.py")
    if os.path.exists(runtime_path):
        with open(runtime_path) as f: c = f.read()
        if "from prompts.loader import" in c or "from prompts.renderer import" in c:
            warnings.append("OK: safe_generate imports prompts runtime")
        else:
            critical.append("safe_generate does NOT import prompts runtime")
        if "from agent.llm.tasks.prompts import PROMPTS" in c and "Exception" not in c:
            # Check if PROMPTS import is in fallback path
            lines = c.split("\n")
            prompts_line = None
            fallback_near = False
            for i, l in enumerate(lines):
                if "from agent.llm.tasks.prompts import" in l:
                    prompts_line = i
                if prompts_line and i - prompts_line < 10 and ("fallback" in l.lower() or "except" in l.lower()):
                    fallback_near = True
            if prompts_line and not fallback_near:
                critical.append("safe_generate defaults to old PROMPTS (not in fallback)")

        if "rendered.text" in c:
            warnings.append("OK: rendered.text used in messages")
        else:
            high.append("rendered.text does NOT enter provider messages")

    # Check composer
    composer_path = os.path.join(ROOT, "agent", "nodes", "composer.py")
    if os.path.exists(composer_path):
        with open(composer_path) as f: c = f.read()
        if "_select_prompt_task" in c:
            warnings.append("OK: composer has _select_prompt_task")
        else:
            critical.append("composer missing _select_prompt_task")

    # Check policy
    policy_path = os.path.join(ROOT, "prompts", "policy.py")
    if os.path.exists(policy_path):
        with open(policy_path) as f: c = f.read()
        if "check_prompt_input" in c: warnings.append("OK: check_prompt_input exists")
        if "check_prompt_output" in c: warnings.append("OK: check_prompt_output exists")
        if "FAKE_REF_PATTERN" in c: warnings.append("OK: fake ref pattern exists")
        if "已修改生产环境" in c or "production-ready" in c.lower(): warnings.append("OK: unverified execution detection")
        if "detect_prompt_injection" in c: warnings.append("OK: injection detection exists")

    result = {
        "audit": "prompt_runtime",
        "critical_count": len(critical), "high_count": len(high),
        "critical": critical, "high": high, "warnings": warnings,
        "conclusion": "PASS" if not critical and not high else "FAIL",
    }

    for fmt, name in [("json", "prompt_runtime_audit.json"), ("md", "PROMPT_RUNTIME_AUDIT.md")]:
        p = os.path.join(REPORTS, name)
        with open(p, "w") as f:
            if fmt == "json": json.dump(result, f, indent=2)
            else: f.write(f"# Prompt Runtime Audit\n\n**Conclusion: {result['conclusion']}**\n")

    print(f"Prompt Runtime Audit: {result['conclusion']} (C:{len(critical)} H:{len(high)})")

if __name__ == "__main__":
    run()
