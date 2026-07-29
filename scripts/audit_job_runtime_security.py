#!/usr/bin/env python3
"""Job Runtime Security Audit."""
import json, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTS = os.path.join(ROOT, "reports")
os.makedirs(REPORTS, exist_ok=True)

critical, high, warnings = [], [], []

def run():
    # Check jobs/ module
    for mod in ["jobs/schemas.py", "jobs/store.py", "jobs/manager.py", "jobs/runner.py", "jobs/worker.py", "jobs/redaction.py"]:
        if os.path.exists(os.path.join(ROOT, mod)):
            warnings.append(f"OK: {mod} exists")
        else:
            critical.append(f"{mod} missing")

    # Check redaction
    red_path = os.path.join(ROOT, "jobs", "redaction.py")
    if os.path.exists(red_path):
        with open(red_path) as f: c = f.read()
        if "source_config" in c and "REDACTED" in c:
            warnings.append("OK: job redaction covers source_config")
        if "sanitize_job_log" in c:
            warnings.append("OK: log sanitization present")

    # Check runner uses run_agent not direct module
    runner_path = os.path.join(ROOT, "jobs", "runner.py")
    if os.path.exists(runner_path):
        with open(runner_path) as f: c = f.read()
        if "run_agent" in c:
            warnings.append("OK: runner calls run_agent()")
        if "from modules." in c or "import modules." in c:
            high.append("runner imports modules directly")

    # Check source_config_ref summary safe
    mgr_path = os.path.join(ROOT, "jobs", "manager.py")
    if os.path.exists(mgr_path):
        with open(mgr_path) as f: c = f.read()
        if "source_config[:80]" in c:
            critical.append("source_config_ref summary uses raw text slice")
        if "Config content stored as artifact reference" in c:
            warnings.append("OK: source_config_ref uses safe summary")

    # Check append_log sanitization
    store_path = os.path.join(ROOT, "jobs", "store.py")
    if os.path.exists(store_path):
        with open(store_path) as f: c = f.read()
        if "sanitize_job_log_for_storage" in c:
            warnings.append("OK: append_log uses sanitize_job_log_for_storage")
        else:
            high.append("append_log missing sanitize_job_log_for_storage call")

    # Check state machine
    if os.path.exists(mgr_path):
        with open(mgr_path) as f: c = f.read()
        if "ALLOWED_TRANSITIONS" in c:
            warnings.append("OK: ALLOWED_TRANSITIONS table present")

    # Check no direct status update in runner
    if os.path.exists(runner_path):
        with open(runner_path) as f: c = f.read()
        if 'update_job(ws_id, job_id, {"status": "succeeded"})' in c:
            critical.append("runner bypasses state machine with direct status= succeeded")

    result = {
        "audit": "job_runtime_security",
        "critical_count": len(critical), "high_count": len(high),
        "critical": critical, "high": high, "warnings": warnings,
        "conclusion": "PASS" if not critical and not high else "FAIL",
    }

    for fmt, name in [("json", "job_runtime_security_audit.json"), ("md", "JOB_RUNTIME_SECURITY_AUDIT.md")]:
        p = os.path.join(REPORTS, name)
        with open(p, "w") as f:
            if fmt == "json":
                json.dump(result, f, indent=2)
            else:
                f.write(f"# Job Runtime Security Audit\n\n**Conclusion: {result['conclusion']}**\n\n")
                for label, items in [("Critical", critical), ("High", high), ("Warnings", warnings)]:
                    if items:
                        f.write(f"## {label}\n")
                        for i in items: f.write(f"- {i}\n")
                        f.write("\n")

    print(f"Job Runtime Security Audit: {result['conclusion']} (C:{len(critical)} H:{len(high)})")

if __name__ == "__main__":
    run()
