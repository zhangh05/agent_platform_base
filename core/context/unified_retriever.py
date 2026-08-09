# context/unified_retriever.py
"""UnifiedRetriever — single BM25 engine for memory + knowledge retrieval.

Provides a shared BM25 scoring layer over ContextStore items. Callers
filter by ``item_type`` (``memory_hit``, ``knowledge_chunk``, …) to
scope results. Higher-level retrievers wrap this:

  - ``agent/runtime/memory/retriever.py``  → memory_hit only
  - ``agent/modules/knowledge/index.py``  → knowledge_chunk only

Features:
  - Field-weighted BM25 (title > section/chapter > content)
  - CJK bigram/trigram tokenization
  - Scope boosting (session > workspace > global)
  - Jaccard sibling dedup
  - Query expansion via a small static platform-term dictionary
  - Unified result schema

v3.1.0: Created as part of P1-P5 refactoring.
"""

from __future__ import annotations

import math
import re
import os
import time
import threading
from collections import Counter, defaultdict
from typing import Callable, Optional
from pathlib import Path

from core.context.context_store import get_context_store
from storage.memory_governance import SUPPORTED_MEMORY_TYPES


# ---------------------------------------------------------------------------
# Tokenization (shared for indexing and querying)
# ---------------------------------------------------------------------------

_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
_WORD_RE = re.compile(r"[a-zA-Z0-9_\-\.]+|[\u4e00-\u9fff\u3400-\u4dbf]")

def tokenize(text: str, cjk_ngram_ns: tuple[int, ...] = (1, 2)) -> list[str]:
    """Tokenize text into terms. CJK uses n-gram with stopword filter; Latin uses word split.

    v3.2: Added unigram to improve short-query recall. Added CJK stopword filter
    to remove semantically meaningless n-grams (particles, punctuation fragments).
    """
    if not text:
        return []
    text = text.lower()
    tokens: list[str] = []

    # Latin words
    for m in _WORD_RE.finditer(text):
        w = m.group()
        if len(w) > 1 or not _CJK_RE.match(w):
            tokens.append(w)

    # CJK n-grams with stopword filter
    cjk_chars = _CJK_RE.findall(text)
    cjk_str = "".join(cjk_chars)
    for n in cjk_ngram_ns:
        for i in range(len(cjk_str) - n + 1):
            token = cjk_str[i:i + n]
            if token not in _CJK_STOPWORDS:
                tokens.append(token)

    return tokens


# CJK stopwords — meaningless n-grams that add noise
_CJK_STOPWORDS: set[str] = {
    # Common function particles (bigrams)
    "的是", "了这", "在那", "的一", "了不", "了个", "是这",
    "之的", "为的", "所这", "和其", "于这", "被那",
    "这个", "那个", "一个", "这种", "那种", "一些", "这些",
    "我们", "他们", "你们", "它一", "自一",
    # Single characters that appear as unigrams (too short/generic)
    "的", "了", "是", "在", "和", "与", "之", "为",
    "所", "以", "这", "那", "一", "不", "也", "有",
    "人", "要", "会", "就", "能", "对", "说", "向",
    "用", "被", "当", "但", "从", "而", "去",
    # Punctuation-near CJK (common noise)
    "由一", "因这", "如此", "因此",
}


# ---------------------------------------------------------------------------
# Generic platform query expansion (static dictionary)
# ---------------------------------------------------------------------------

_QUERY_SYNONYMS: dict[str, list[str]] = {
    # ── 工作区 / 数据 ──
    "工作区": ["workspace", "项目", "空间"],
    "workspace": ["工作区", "project"],
    "项目": ["project", "工作区"],
    "数据": ["data", "资料", "文件"],
    "文件": ["file", "document", "资料"],
    "资料": ["document", "data", "文件"],
    "文档": ["document", "knowledge", "知识"],
    "知识": ["knowledge", "文档"],
    # ── 运行 / 作业 ──
    "任务": ["task", "job", "run"],
    "作业": ["job", "task", "run"],
    "运行": ["run", "runtime", "execution"],
    "会话": ["session", "conversation"],
    "状态": ["status", "state"],
    "结果": ["result", "output", "outcome"],
    "证据": ["evidence", "artifact", "reference"],
    "制品": ["artifact", "output", "evidence"],
    # ── 质量 / 运维 ──
    "排查": ["troubleshoot", "debug", "诊断", "排错", "troubleshooting"],
    "诊断": ["diagnose", "排查", "troubleshoot"],
    "监控": ["monitor", "monitoring", "watch", "观察"],
    "备份": ["backup", "save", "保存"],
    "恢复": ["restore", "recovery", "还原"],
    "升级": ["upgrade", "update", "更新"],
}

def expand_query(query: str) -> str:
    """Add generic platform synonyms to the query."""
    terms = tokenize(query)
    expanded = set(terms)
    for t in terms:
        if t in _QUERY_SYNONYMS:
            expanded.update(_QUERY_SYNONYMS[t])
    # Return original query + expansions
    return query + " " + " ".join(expanded - set(terms))


# ---------------------------------------------------------------------------
# Scope boost factors
# ---------------------------------------------------------------------------

_SCOPE_BOOST = {
    "session": 1.5,
    "workspace": 1.2,
    "global": 1.0,
}

# ---------------------------------------------------------------------------
# Field weights
# ---------------------------------------------------------------------------

_FIELD_WEIGHTS = {
    "title": 3.0,
    "chapter": 2.0,
    "section": 2.0,
    "tags": 2.5,
    "summary": 1.5,
    "index_text": 1.2,
    "content": 1.0,
}


# ---------------------------------------------------------------------------
# BM25 Engine
# ---------------------------------------------------------------------------

class _BM25:
    """Minimal BM25 implementation over a list of doc dicts."""

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.docs: list[dict] = []
        self.doc_lens: list[int] = []
        self.avgdl: float = 0
        self.df: dict[str, int] = defaultdict(int)     # term -> doc frequency
        self.tf: list[dict[str, float]] = []            # per-doc term -> weighted freq
        self.n: int = 0
        self._built = False

    def fit(self, docs: list[dict]):
        """Build index from doc dicts."""
        self.docs = docs
        self.n = len(docs)
        self.doc_lens = []
        self.tf = []
        self.df = defaultdict(int)

        for doc in docs:
            tf_counter: Counter = Counter()
            total_len = 0
            for field, weight in _FIELD_WEIGHTS.items():
                text = doc.get(field, "")
                if isinstance(text, list):
                    text = " ".join(str(t) for t in text)
                elif not isinstance(text, str):
                    text = str(text) if text else ""
                terms = tokenize(text)
                total_len += len(terms)
                for t in terms:
                    tf_counter[t] += weight

            self.doc_lens.append(total_len)
            self.tf.append(dict(tf_counter))

            for t in set(tf_counter.keys()):
                self.df[t] += 1

        self.avgdl = sum(self.doc_lens) / max(self.n, 1)
        self._built = True

    def score(self, query: str, top_k: int = 10) -> list[tuple[int, float]]:
        """Return [(doc_index, score)] sorted descending."""
        if not self._built or self.n == 0:
            return []

        q_terms = tokenize(query)
        if not q_terms:
            return []

        scores: list[float] = [0.0] * self.n
        for qt in q_terms:
            if qt not in self.df:
                continue
            idf = math.log((self.n - self.df[qt] + 0.5) / (self.df[qt] + 0.5) + 1.0)
            for i in range(self.n):
                tf_val = self.tf[i].get(qt, 0.0)
                if tf_val == 0:
                    continue
                dl = self.doc_lens[i]
                tf_norm = (tf_val * (self.k1 + 1)) / (
                    tf_val + self.k1 * (1 - self.b + self.b * dl / self.avgdl)
                )
                scores[i] += idf * tf_norm

        # Apply scope boost
        for i, doc in enumerate(self.docs):
            scope = doc.get("scope", "global")
            scores[i] *= _SCOPE_BOOST.get(scope, 1.0)

        # Rank
        ranked = [(i, s) for i, s in enumerate(scores) if s > 0]
        ranked.sort(key=lambda x: x[1], reverse=True)
        return ranked[:top_k]


def rank_documents(query: str, documents: list[dict], top_k: int = 10) -> list[dict]:
    """Rank an explicit document set with the shared BM25 implementation.

    Lifecycle management screens use this for pending/conflict records that
    intentionally do not exist in the active ContextStore projection.
    """
    if not str(query or "").strip() or not documents:
        return documents[:max(0, int(top_k))]
    engine = _BM25()
    engine.fit(documents)
    ranked = engine.score(expand_query(str(query)), top_k=max(1, int(top_k)))
    return [dict(documents[index], _score=round(score, 4)) for index, score in ranked]


# ---------------------------------------------------------------------------
# UnifiedRetriever
# ---------------------------------------------------------------------------

class UnifiedRetriever:
    """Single retriever for all item types in a workspace."""

    def __init__(self, workspace_id: str = "default", *, store=None):
        self.workspace_id = workspace_id
        self._store = store or get_context_store(workspace_id)
        self._bm25 = _BM25()
        self._indexed_count = 0
        self._last_index_time = 0.0
        self._indexed_signature: tuple[int, int] = (-1, -1)
        self._lock = threading.RLock()

    def _store_signature(self) -> tuple[int, int]:
        path = self._store._items_path
        try:
            stat = path.stat()
            return stat.st_mtime_ns, stat.st_size
        except OSError:
            return 0, 0

    def _maybe_reindex(self):
        """Rebuild BM25 index if store has changed."""
        signature = self._store_signature()
        if signature != self._indexed_signature or (
            time.time() - self._last_index_time > 30
        ) or not self._bm25._built:
            with self._lock:
                signature = self._store_signature()
                if (
                    signature == self._indexed_signature
                    and self._bm25._built
                    and time.time() - self._last_index_time <= 30
                ):
                    return
                items = self._store.all_items()
                self._bm25.fit(items)
                self._indexed_count = len(items)
                self._indexed_signature = self._store_signature()
                self._last_index_time = time.time()

    def search(
        self,
        query: str,
        item_type: Optional[str] = None,
        item_types: Optional[list[str]] = None,
        scope: Optional[str] = None,
        source_id: Optional[str] = None,
        tags: Optional[list[str]] = None,
        top_k: int = 10,
        min_score: float = 0.01,
        expand: bool = True,
        result_filter: Optional[Callable[[dict], bool]] = None,
    ) -> list[dict]:
        """Search for items matching *query*.

        Args:
            query:      Natural language query.
            item_type:  Filter to a single type (e.g. "memory_hit").
            item_types: Filter to multiple types.
            scope:      Filter by scope.
            source_id:  Filter by source_id.
            tags:       Filter by tags (any-match).
            top_k:      Max results.
            min_score:  Minimum BM25 score threshold.
            expand:     Whether to apply query expansion.
            result_filter: Optional visibility predicate applied before dedup.

        Returns:
            List of item dicts, each with an added ``_score`` field.
        """
        self._maybe_reindex()

        effective_query = expand_query(query) if expand else query
        # Rank the complete index, then apply type/scope filters. Truncating
        # before filtering lets unrelated item types crowd out valid hits.
        raw_results = self._bm25.score(
            effective_query,
            top_k=max(top_k * 3, len(self._bm25.docs)),
        )

        # Post-filter
        results: list[dict] = []
        types_filter = set()
        if item_type:
            types_filter.add(item_type)
        if item_types:
            types_filter.update(item_types)

        candidate_limit = max(top_k * 5, top_k + 20)
        for idx, score in raw_results:
            if score < min_score:
                continue
            doc = self._bm25.docs[idx]

            if doc.get("disabled") is True:
                continue
            if str(doc.get("workspace_id") or "") != self.workspace_id:
                continue
            if result_filter is not None and not result_filter(doc):
                continue

            # Type filter
            if types_filter and doc.get("item_type") not in types_filter:
                continue
            # Scope filter
            if scope and doc.get("scope") != scope:
                continue
            # Source filter
            if source_id and doc.get("source_id") != source_id:
                continue
            # Tags filter
            if tags:
                doc_tags = set(doc.get("tags") or [])
                if not doc_tags.intersection(tags):
                    continue

            hit = dict(doc)
            hit["_score"] = round(score, 4)
            results.append(hit)

            if len(results) >= candidate_limit:
                break

        # Apply post-score boosts: recency, confirmation, frequency
        results = self._apply_boosts(results)

        # Dedup by content similarity (Jaccard on tokens)
        results = self._dedup_results(results)

        return results[:top_k]

    def search_memory(
        self,
        query: str,
        top_k: int = 5,
        *,
        session_id: str = "",
        task_id: str = "",
        **kwargs,
    ) -> list[dict]:
        """Convenience: search memory_hit items only."""
        # Retrieve extra candidates before enforcing the governance lifecycle.
        # Pending, rejected, expired and conflict records are not prompt facts.
        candidates = self.search(
            query,
            item_type="memory_hit",
            top_k=top_k,
            result_filter=lambda hit: (
                str(hit.get("memory_status") or hit.get("status") or "").lower()
                in {"active", "confirmed"}
                and str(hit.get("memory_type") or "") in SUPPORTED_MEMORY_TYPES
                and self._memory_scope_visible(
                    hit, session_id=session_id, task_id=task_id
                )
            ),
            **kwargs,
        )
        return candidates[:top_k]

    def search_knowledge(self, query: str, top_k: int = 5, **kwargs) -> list[dict]:
        """Convenience: search knowledge_chunk items only."""
        return self.search(query, item_type="knowledge_chunk", top_k=top_k, **kwargs)

    def retrieve_for_context(
        self,
        query: str,
        top_k_memory: int = 5,
        top_k_knowledge: int = 5,
        session_id: str = "",
        task_id: str = "",
    ) -> dict:
        """Retrieve both memory and knowledge hits for context building.

        Returns:
            {"memory_hits": [...], "knowledge_hits": [...]}
        """
        memory = self.search_memory(
            query,
            top_k=top_k_memory,
            session_id=session_id,
            task_id=task_id,
        )
        knowledge = self.search_knowledge(query, top_k=top_k_knowledge)
        return {
            "memory_hits": memory,
            "knowledge_hits": knowledge,
        }

    def _memory_scope_visible(
        self,
        hit: dict,
        *,
        session_id: str,
        task_id: str,
    ) -> bool:
        scope = str(hit.get("scope") or "").lower()
        if scope == "global":
            return True
        if scope == "workspace":
            return str(hit.get("workspace_id") or "") == self.workspace_id
        if scope == "session":
            return bool(session_id) and str(hit.get("session_id") or "") == session_id
        if scope == "task":
            return bool(task_id) and str(hit.get("task_id") or "") == task_id
        return False

    @staticmethod
    def _apply_boosts(results: list[dict]) -> list[dict]:
        """Apply authority, memory-layer, and type-specific time boosts."""
        if not results:
            return results

        now = time.time()
        for hit in results:
            boost = 1.0
            score = hit.get("_score", 0.0)

            memory_type = str(hit.get("memory_type") or "")
            authority = str(hit.get("authority") or "")
            authority_rank = int(hit.get("authority_rank") or 0)
            if authority == "explicit_user" or authority_rank >= 100:
                boost *= 2.5
            elif authority == "manual_confirm" or authority_rank >= 80:
                boost *= 2.0
            elif authority == "verified_tool" or authority_rank >= 60:
                boost *= 1.5
            elif authority == "agent_inference":
                boost *= 0.8

            if memory_type == "core_rule":
                boost *= 2.0
            elif memory_type == "procedural_rule":
                boost *= 1.25

            # Recency helps rank comparable cases. It must never demote a
            # durable rule or stable semantic fact merely because it is old.
            created_at = hit.get("created_at", "")
            if memory_type == "episodic_case" and created_at:
                try:
                    age_s = now - _ts_to_epoch(created_at)
                    if age_s <= 0:
                        pass
                    elif age_s < 86400:
                        boost *= 1.25
                    elif age_s < 604800:
                        boost *= 1.1
                except Exception:
                    pass

            # ── Confirmation boost ──
            status = str(hit.get("status", "")).lower()
            if status in ("active", "confirmed"):
                boost *= 1.3

            hit["_boost"] = round(boost, 3)
            hit["_score"] = round(score * boost, 4)

        # Re-sort by boosted score
        results.sort(key=lambda h: -h["_score"])
        return results

    @staticmethod
    def _dedup_results(results: list[dict], threshold: float = 0.75) -> list[dict]:
        """Remove near-duplicate results by Jaccard similarity on content tokens."""
        if len(results) <= 1:
            return results

        kept: list[dict] = []
        kept_tokens: list[set[str]] = []

        for r in results:
            content = r.get("content", "")
            if isinstance(content, dict):
                content = str(content)
            toks = set(tokenize(content))
            if not toks:
                kept.append(r)
                kept_tokens.append(toks)
                continue

            is_dup = False
            for kt in kept_tokens:
                if not kt:
                    continue
                jaccard = len(toks & kt) / len(toks | kt)
                if jaccard > threshold:
                    is_dup = True
                    break

            if not is_dup:
                kept.append(r)
                kept_tokens.append(toks)

        return kept


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts_to_epoch(ts: str) -> float:
    """Parse ISO 8601 timestamp to epoch seconds. Returns 0 on failure."""
    import datetime
    try:
        ts = ts.replace("Z", "+00:00")
        return datetime.datetime.fromisoformat(ts).timestamp()
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# Singleton helper
# ---------------------------------------------------------------------------
_retrievers: dict[tuple[str, str], UnifiedRetriever] = {}
_retrievers_lock = threading.Lock()

def get_retriever(workspace_id: str = "default") -> UnifiedRetriever:
    """Return the singleton retriever for the current user/workspace store."""
    store = get_context_store(workspace_id)
    key = (store.workspace_id, str(store._items_path))
    with _retrievers_lock:
        if key not in _retrievers:
            _retrievers[key] = UnifiedRetriever(store.workspace_id, store=store)
        return _retrievers[key]
