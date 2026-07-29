# agent/modules/knowledge/schemas.py
"""Data models for v1.0.1 document ingestion and book library.

Three primary data classes:

  NormalizedDocument
    Intermediate representation produced by parsers/, before chunking.
    Carries normalized_markdown, source_type, scope, language, metadata,
    warnings. Plain dataclass (no I/O).

  KnowledgeSource
    Top-level metadata for an imported document (book / manual / RFC /
    project_doc / attachment). One source_id per imported file. Stored
    in the v1.0 KnowledgeStore under artifact_type=knowledge_source
    (or alongside the source record).

  KnowledgeChunk
    A single chunk of text derived from a source. Two types: parent
    (chapter / section, 1200–3000 chars) and child (400–800 chars with
    overlap). Children are what retrieval returns; parents can be
    fetched on demand via knowledge.read.

The chunk store is unified ContextStore (items.jsonl) under the workspace context dir
as the v1.0 source store. Scope (global / workspace / session) is
encoded in each chunk's metadata so retrieval can filter without
ambiguity.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


SUPPORTED_FORMATS = ("md", "markdown", "txt", "html", "htm",
                     "docx", "pdf")

SOURCE_TYPES = ("book", "manual", "rfc", "project_doc", "attachment", "memory")

SCOPES = ("global", "workspace", "session")

# Scope priority order for retrieval: session > workspace > global.
SCOPE_PRIORITY = {"session": 3, "workspace": 2, "global": 1}

# Chunk parameters (per spec v1.0.1 § 3)
CHILD_TARGET = 600      # target middle of 400-800 chars
CHILD_MIN = 180
CHILD_MAX = 1200
CHILD_OVERLAP = 80
PARENT_MIN = 1200
PARENT_MAX = 3000


@dataclass
class NormalizedDocument:
    """Intermediate, format-agnostic representation of a parsed document.

    Produced by parsers/, consumed by chunking.py + ingestion.py.
    """
    source_id: str = ""
    title: str = ""
    author: str = ""
    edition: str = ""
    source_type: str = "project_doc"   # book / manual / rfc / project_doc / attachment
    scope: str = "workspace"           # global / workspace / session
    language: str = "zh"
    format: str = "md"
    normalized_markdown: str = ""
    metadata: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "NormalizedDocument":
        if not isinstance(d, dict):
            return cls()
        return cls(
            source_id=str(d.get("source_id", "")),
            title=str(d.get("title", "")),
            author=str(d.get("author", "")),
            edition=str(d.get("edition", "")),
            source_type=str(d.get("source_type", "project_doc")),
            scope=str(d.get("scope", "workspace")),
            language=str(d.get("language", "zh")),
            format=str(d.get("format", "md")),
            normalized_markdown=str(d.get("normalized_markdown", "")),
            metadata=dict(d.get("metadata") or {}),
            warnings=list(d.get("warnings") or []),
        )


@dataclass
class KnowledgeSource:
    """Top-level metadata for an imported document.

    Stored in the v1.0 KnowledgeStore content under title=
    "[source] {title}".  This is a *view* over the store; the
    actual persistent record is the KnowledgeStore's SourceRecord
    (see agent.modules.knowledge.store).
    """
    source_id: str
    title: str
    author: str = ""
    edition: str = ""
    source_type: str = "project_doc"
    scope: str = "workspace"
    format: str = "md"
    language: str = "zh"
    tags: list = field(default_factory=list)
    enabled: bool = True
    created_at: str = ""
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "KnowledgeSource":
        if not isinstance(d, dict):
            return cls(source_id="", title="")
        return cls(
            source_id=str(d.get("source_id", "")),
            title=str(d.get("title", "")),
            author=str(d.get("author", "")),
            edition=str(d.get("edition", "")),
            source_type=str(d.get("source_type", "project_doc")),
            scope=str(d.get("scope", "workspace")),
            format=str(d.get("format", "md")),
            language=str(d.get("language", "zh")),
            tags=list(d.get("tags") or []),
            enabled=bool(d.get("enabled", True)),
            created_at=str(d.get("created_at", "")),
            metadata=dict(d.get("metadata") or {}),
        )


@dataclass
class KnowledgeChunk:
    """A single chunk of a KnowledgeSource.

    chunk_type is either "parent" (chapter / section, 1200-3000 chars)
    or "child" (400-800 chars with overlap). Children are what
    retrieval returns; parents can be expanded on demand.
    """
    chunk_id: str
    source_id: str
    parent_chunk_id: str = ""
    chunk_type: str = "child"  # parent | child
    chapter: str = ""
    section: str = ""
    subsection: str = ""
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    chunk_index: int = 0
    content: str = ""
    index_text: str = ""       # title + chapter + section + tags + body
                                # (used for retrieval; body content
                                #  is also kept verbatim in `content`)
    token_count: int = 0
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "KnowledgeChunk":
        if not isinstance(d, dict):
            return cls(chunk_id="", source_id="")
        return cls(
            chunk_id=str(d.get("chunk_id", "")),
            source_id=str(d.get("source_id", "")),
            parent_chunk_id=str(d.get("parent_chunk_id", "")),
            chunk_type=str(d.get("chunk_type", "child")),
            chapter=str(d.get("chapter", "")),
            section=str(d.get("section", "")),
            subsection=str(d.get("subsection", "")),
            page_start=d.get("page_start"),
            page_end=d.get("page_end"),
            chunk_index=int(d.get("chunk_index", 0)),
            content=str(d.get("content", "")),
            index_text=str(d.get("index_text", "")),
            token_count=int(d.get("token_count", 0)),
            metadata=dict(d.get("metadata") or {}),
        )
