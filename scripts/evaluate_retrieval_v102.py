#!/usr/bin/env python3
"""scripts/evaluate_retrieval_v102.py

Run the v1.0.2 retrieval quality evaluation.

Pipeline:
  1. Load fixture (harness/fixtures/retrieval_eval_v102.json).
  2. Build a fresh workspace under temp_dirs (per conftest).
  3. Ingest each fixture document via import_file (markdown
     format) with the doc's chapter/section structure.
  4. For each fixture query, run search_chunks; record whether
     the top-1/3/5 hits contain the expected doc_id + chapter
     substring.
  5. Compute metrics:
     - Recall@1, Recall@3, Recall@5
     - MRR
     - no-hit precision (queries with expected=null must have
       source_count=0)
     - duplicate rate (queries with duplicate_siblings type)
  6. Print a JSON report to stdout.
  7. Exit code 0 if all thresholds pass; 1 otherwise.

Usage:
  python scripts/evaluate_retrieval_v102.py [--fixture PATH] [--top-k N]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path
from typing import List, Optional


# Force the workspace root into a temp dir so we don't pollute
# real workspaces.
_TMP_ROOT = Path(tempfile.mkdtemp(prefix="knowledge_eval_v102_"))
os.environ["LZCORE_WORKSPACE_ROOT"] = str(_TMP_ROOT)
os.environ["LZCORE_WORKSPACE_DIR"] = str(_TMP_ROOT)

# Also override the artifacts fallback path if used.
try:
    import artifacts.store as _astore
    if hasattr(_astore, "_get_ws_root"):
        # Patch _get_ws_root to return _TMP_ROOT
        def _patched_get_ws_root():
            return _TMP_ROOT
        _astore._get_ws_root = _patched_get_ws_root
except Exception:
    pass

from agent.modules.knowledge.ingestion import import_file
from agent.modules.knowledge.index import search_chunks


DEFAULT_FIXTURE = (
    Path(__file__).parent.parent
    / "harness" / "fixtures" / "retrieval_eval_v102.json"
)


def _flatten_doc(doc: dict) -> str:
    """Build a markdown body for a fixture document."""
    lines = [f"# {doc['title']}", ""]
    for ch in doc.get("chapters", []):
        lines.append(f"## {ch['chapter']}")
        lines.append("")
        for sec in ch.get("sections", []):
            lines.append(f"### {sec['section']}")
            lines.append("")
            lines.append(sec.get("content", ""))
            lines.append("")
    return "\n".join(lines)


def _ingest_documents(workspace_id: str, docs: List[dict]) -> dict:
    """Ingest fixture docs into a fresh workspace. Returns
    {doc_id: source_id} map."""
    doc_id_to_source_id: dict = {}
    for d in docs:
        body = _flatten_doc(d)
        out = import_file(
            workspace_id=workspace_id,
            source=body.encode("utf-8"),
            title=d["title"],
            source_type=d.get("source_type", "book"),
            scope=d.get("scope", "workspace"),
            language=d.get("language", "zh"),
            tags=list(d.get("tags") or []),
            metadata={
                "fixture_doc_id": d["doc_id"],
            },
        )
        if not out.get("ok"):
            return {"error": f"ingest_failed: {d['doc_id']}: {out}"}
        doc_id_to_source_id[d["doc_id"]] = out["source_id"]
    return doc_id_to_source_id


def _hit_matches_expected(hit: dict, expected_doc_id: Optional[str],
                            doc_id_to_source_id: dict,
                            expected_chapter_substrings: List[str]) -> bool:
    """A hit matches if its source_id is the expected source AND
    the chapter/section contain at least one expected substring.
    """
    if expected_doc_id is None:
        return False
    expected_source_id = doc_id_to_source_id.get(expected_doc_id)
    if not expected_source_id:
        return False
    if hit.get("source_id") != expected_source_id:
        return False
    if not expected_chapter_substrings:
        return True
    for sub in expected_chapter_substrings:
        if (sub in (hit.get("chapter") or "")
            or sub in (hit.get("section") or "")
            or sub in (hit.get("subsection") or "")
            or sub in (hit.get("title") or "")):
            return True
    return False


def evaluate(fixture_path: Path, top_k: int = 5,
              verbose: bool = True) -> dict:
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    thresholds = fixture.get("thresholds", {})
    docs = fixture.get("documents", [])
    queries = fixture.get("queries", [])

    workspace_id = "eval_v102_workspace"

    # Ingest.
    # All non-result output (progress, debug) goes to stderr so stdout
    # is reserved for the final JSON report (parseable by callers in
    # --quiet mode).
    def _out(*args, **kwargs):
        if verbose:
            print(*args, file=sys.stderr, **kwargs)

    _out(f"[eval] workspace = {workspace_id}")
    _out(f"[eval] tmp_root  = {_TMP_ROOT}")
    ingest_result = _ingest_documents(workspace_id, docs)
    if "error" in ingest_result:
        return {"ok": False, "error": ingest_result["error"]}
    doc_id_to_source_id = ingest_result
    _out(f"[eval] ingested {len(docs)} docs, source_ids = {list(doc_id_to_source_id.values())}")

    # Run queries.
    per_query = []
    for q in queries:
        kwargs = {
            "workspace_id": workspace_id,
            "query": q["query"],
            "top_k": top_k,
        }
        if q.get("filter_source_id"):
            # filter_source_id may be a real source_id OR a doc_id
            # placeholder (e.g. "ospf_book_zh_source_id" used as a
            # logical key in the fixture). Resolve to the real
            # source_id when possible.
            f = q["filter_source_id"]
            kwargs["source_id"] = doc_id_to_source_id.get(f, f)
        try:
            r = search_chunks(**kwargs)
        except Exception as e:
            per_query.append({
                "id": q["id"], "type": q["type"], "query": q["query"],
                "ok": False, "error": f"exception: {e!r}",
                "traceback": traceback.format_exc(),
            })
            continue
        hits = r.get("hits") or []
        # Debug for queries that don't pass recall_at_3
        if hits and not any(_hit_matches_expected(
                h, q.get("expected_doc_id"),
                doc_id_to_source_id,
                q.get("expected_chapter_substrings") or [])
            for h in hits[:3]):
            _out(f"\n[debug] query '{q['query']}' (id={q['id']}) "
                 f"did not pass recall@3. Top-3 hits:")
            for h in hits[:3]:
                _out(f"  score={h['score']:.3f}  lex={h['lexical_score']:.3f}  "
                     f"ch={h.get('chapter','')!r}  sec={h.get('section','')!r}  "
                     f"sub={h.get('subsection','')!r}  "
                     f"snippet={h.get('snippet','')[:60]!r}")
        # Debug for no-hit queries
        if q.get("expected_doc_id") is None and hits:
            _out(f"\n[debug] no-hit query '{q['query']}' got {len(hits)} hits:")
            for h in hits[:5]:
                _out(f"  score={h['score']:.3f}  lex={h['lexical_score']:.3f}  "
                     f"chapter={h.get('chapter','')!r}  "
                     f"snippet={h.get('snippet','')[:60]!r}")
        # Recall@k: top-k hits contain at least one matching hit?
        def _match(hit):
            return _hit_matches_expected(
                hit, q.get("expected_doc_id"),
                doc_id_to_source_id,
                q.get("expected_chapter_substrings") or [])
        recall_at = {}
        for k in (1, 3, 5):
            recall_at[k] = any(_match(h) for h in hits[:k])
        # MRR
        mrr = 0.0
        for i, h in enumerate(hits, start=1):
            if _match(h):
                mrr = 1.0 / i
                break
        # no-hit
        no_hit_expected = q.get("expected_doc_id") is None
        no_hit_correct = no_hit_expected and len(hits) == 0
        per_query.append({
            "id": q["id"], "type": q["type"], "query": q["query"],
            "ok": True,
            "source_count": len(hits),
            "top_hit_source": hits[0]["source_id"] if hits else None,
            "top_hit_chapter": hits[0].get("chapter", "") if hits else "",
            "top_hit_title": hits[0].get("title", "") if hits else "",
            "top_hit_score": hits[0].get("score", 0) if hits else 0,
            "recall_at_1": bool(recall_at[1]),
            "recall_at_3": bool(recall_at[3]),
            "recall_at_5": bool(recall_at[5]),
            "mrr": round(mrr, 3),
            "no_hit_expected": no_hit_expected,
            "no_hit_correct": no_hit_correct,
            "deduplicated_count": (r.get("metadata") or {}).get(
                "deduplicated_count", 0),
            "pre_dedup_count": (r.get("metadata") or {}).get(
                "pre_dedup_count", len(hits)),
            "query_expansions": (r.get("metadata") or {}).get(
                "query_expansions", []),
        })

    # Aggregate metrics.
    ok_qs = [p for p in per_query if p.get("ok")]
    n = len(ok_qs)
    recall_at_1 = sum(1 for p in ok_qs if p.get("recall_at_1")) / max(n, 1)
    recall_at_3 = sum(1 for p in ok_qs if p.get("recall_at_3")) / max(n, 1)
    recall_at_5 = sum(1 for p in ok_qs if p.get("recall_at_5")) / max(n, 1)
    mrr = sum(p.get("mrr", 0.0) for p in ok_qs) / max(n, 1)
    no_hit_qs = [p for p in ok_qs if p.get("no_hit_expected")]
    no_hit_precision = (
        sum(1 for p in no_hit_qs if p.get("no_hit_correct"))
        / max(len(no_hit_qs), 1)
    )
    # Duplicate rate: average (deduplicated_count / pre_dedup_count)
    # across queries where pre_dedup_count > 0.
    dup_rates = []
    for p in ok_qs:
        pre = p.get("pre_dedup_count", 0)
        dropped = p.get("deduplicated_count", 0)
        if pre > 0:
            dup_rates.append(dropped / pre)
    duplicate_rate = sum(dup_rates) / max(len(dup_rates), 1) if dup_rates else 0.0

    metrics = {
        "ok": True,
        "thresholds": thresholds,
        "n_queries": n,
        "n_documents": len(docs),
        "metrics": {
            "recall_at_1": round(recall_at_1, 4),
            "recall_at_3": round(recall_at_3, 4),
            "recall_at_5": round(recall_at_5, 4),
            "mrr": round(mrr, 4),
            "no_hit_precision": round(no_hit_precision, 4),
            "duplicate_rate": round(duplicate_rate, 4),
        },
        "passes": {
            "recall_at_3": recall_at_3 >= thresholds.get("recall_at_3", 0.85),
            "mrr": mrr >= thresholds.get("mrr", 0.75),
            "no_hit_precision": no_hit_precision >= thresholds.get(
                "no_hit_precision", 1.0),
            "duplicate_rate_max": duplicate_rate <= thresholds.get(
                "duplicate_rate_max", 0.20),
        },
        "per_query": per_query,
    }
    metrics["all_pass"] = all(metrics["passes"].values())
    return metrics


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", default=str(DEFAULT_FIXTURE))
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    fixture_path = Path(args.fixture)
    if not fixture_path.exists():
        print(f"[eval] fixture not found: {fixture_path}", file=sys.stderr)
        sys.exit(2)
    res = evaluate(fixture_path, top_k=args.top_k, verbose=not args.quiet)
    if args.quiet:
        # Print only the summary.
        print(json.dumps({k: v for k, v in res.items() if k != "per_query"},
                         ensure_ascii=False, indent=2))
    else:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    # Cleanup temp workspace.
    try:
        shutil.rmtree(_TMP_ROOT, ignore_errors=True)
    except Exception:
        pass
    if not res.get("ok"):
        sys.exit(2)
    sys.exit(0 if res.get("all_pass") else 1)


if __name__ == "__main__":
    main()
