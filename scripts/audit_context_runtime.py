#!/usr/bin/env python3
"""Context Runtime Audit."""
import json, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTS = os.path.join(ROOT, "reports")
os.makedirs(REPORTS, exist_ok=True)

critical, high, warnings = [], [], []

def run():
    for mod in ["context/schemas.py", "context/resolver.py", "context/loader.py", "context/selector.py", "context/compressor.py", "context/builder.py"]:
        if os.path.exists(os.path.join(ROOT, mod)):
            warnings.append(f"OK: {mod}")
        else:
            critical.append(f"Missing: {mod}")

    builder_path = os.path.join(ROOT, "context", "builder.py")
    if os.path.exists(builder_path):
        with open(builder_path) as f: c = f.read()
        if "execution_context" in c and "safe_llm_context" in c:
            warnings.append("OK: execution_context and safe_llm_context separated")
        else:
            high.append("execution/safe context separation unclear")
        if "used_items" in c and "used_chars" in c:
            warnings.append("OK: budget has used_items/used_chars")

    result = {
        "audit": "context_runtime",
        "critical_count": len(critical), "high_count": len(high),
        "critical": critical, "high": high, "warnings": warnings,
        "conclusion": "PASS" if not critical and not high else "FAIL",
    }

    for fmt, name in [("json", "context_runtime_audit.json"), ("md", "CONTEXT_RUNTIME_AUDIT.md")]:
        p = os.path.join(REPORTS, name)
        with open(p, "w") as f:
            if fmt == "json": json.dump(result, f, indent=2)
            else: f.write(f"# Context Runtime Audit\n\n**Conclusion: {result['conclusion']}**\n")

    print(f"Context Runtime Audit: {result['conclusion']} (C:{len(critical)} H:{len(high)})")

if __name__ == "__main__":
    run()
