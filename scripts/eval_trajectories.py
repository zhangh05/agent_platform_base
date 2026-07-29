#!/usr/bin/env python3
"""Phase 10: Offline trajectory evaluation script.

Usage:
    python3 scripts/eval_trajectories.py --workspace default
    python3 scripts/eval_trajectories.py --workspace default --format md
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

def main():
    ws_id = "default"
    fmt = "json"
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--workspace" and i + 1 < len(args): ws_id = args[i + 1]
        if a == "--format" and i + 1 < len(args): fmt = args[i + 1]

    try:
        from agent.runtime.durable.trajectory import list_trajectories, evaluate_trajectory
        trajs = list_trajectories(ws_id)
        if not trajs:
            out = {"ok": False, "workspace": ws_id, "total": 0, "message": "no trajectories found"}
            print(json.dumps(out, indent=2) if fmt == "json" else "# No trajectories found")
            return

        results = []
        for t in trajs:
            results.append({
                "trajectory_id": t.get("trajectory_id", ""),
                "task_id": t.get("task_id", ""),
                "final_status": t.get("final_status", ""),
                "eval": evaluate_trajectory(t),
                "metrics": t.get("metrics", {}),
            })

        total = len(results)
        ok_count = sum(1 for r in results if r["eval"]["ok"])
        critical_count = sum(1 for r in results if r["eval"]["severity"] == "critical")

        issues = {}
        for r in results:
            for issue in r["eval"]["issues"]:
                issues[issue] = issues.get(issue, 0) + 1

        summary = {
            "workspace": ws_id, "total": total,
            "ok": ok_count, "critical": critical_count,
            "success_rate": f"{ok_count}/{total}",
            "issues": issues,
            "top_failing_tools": _top_failing_tools(results),
            "trajectories": results if fmt == "json" else None,
        }

        if fmt == "json":
            print(json.dumps(summary, indent=2, ensure_ascii=False))
        else:
            print(f"# Trajectory Eval — workspace={ws_id}")
            print(f"Total: {total} | OK: {ok_count} | Critical: {critical_count} | Success rate: {summary['success_rate']}")
            print()
            print("## Issues")
            for k, v in sorted(issues.items(), key=lambda x: -x[1]):
                print(f"- **{k}**: {v}")
            print()
            print("## Top failing tools")
            for t in summary.get("top_failing_tools", []):
                print(f"- {t}")
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))

def _top_failing_tools(results: list) -> list:
    from collections import Counter
    tools = Counter()
    for r in results:
        metrics = r.get("metrics", {})
        if metrics.get("tool_failure_count", 0) > 0:
            for tc in r.get("tool_calls", []) or []:
                if not tc.get("ok", True):
                    tools[tc.get("tool_id", "unknown")] += 1
    return [f"{t} (×{c})" for t, c in tools.most_common(10)]


if __name__ == "__main__":
    main()
