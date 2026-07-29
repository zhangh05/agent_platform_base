from __future__ import annotations

from core.tools.schemas import ToolInvocation
from storage.ids import validate_workspace_id

from core.tools.general_tools.shared import _caller_workspace, _contract, _error, _error_inv, _ok, _result, _unavailable, _workspace_path
"""Split general tool handlers."""

def handle_pdf_extract_text(inv: ToolInvocation) -> dict:
    """Extract text from a workspace PDF file.

    Uses PyPDF2 if available. Validates PDF format, file size, and page range.
    """
    args = inv.arguments
    ws = _caller_workspace(inv)
    filepath = str(args.get("filepath", "")).strip()
    page_range = str(args.get("page_range", "")).strip()

    if not filepath:
        return _error_inv(inv, "filepath is required")

    # Validate workspace path
    try:
        target = _workspace_path(ws, filepath)
    except ValueError as e:
        return _error_inv(inv, str(e))

    # Check file extension
    if target.suffix.lower() != ".pdf":
        return _error_inv(inv, "file must be a .pdf file")

    if not target.is_file():
        return _error_inv(inv, f"file not found: {filepath}")

    file_size = target.stat().st_size

    # ── File size limit (10MB) ──
    MAX_PDF_SIZE = 10 * 1024 * 1024
    if file_size > MAX_PDF_SIZE:
        return _result(False, {
            "ok": False,
            "error": f"PDF file too large ({file_size} bytes, max {MAX_PDF_SIZE})",
        })

    # ── Validate PDF format: must start with %PDF ──
    try:
        with open(target, "rb") as f:
            header = f.read(4)
        if not header.startswith(b"%PDF"):
            return _result(False, {
                "ok": False,
                "error": "not a PDF file",
                "file_size": file_size,
            })
    except Exception:
        return _error_inv(inv, "not a PDF file")

    # ── Parse and validate page range ──
    start_page = None
    end_page = None
    if page_range:
        try:
            parts = page_range.split("-")
            if len(parts) == 1:
                start_page = int(parts[0].strip()) - 1  # 0-indexed
                end_page = start_page
            elif len(parts) == 2:
                start_page = int(parts[0].strip()) - 1
                end_page = int(parts[1].strip()) - 1
        except (ValueError, IndexError):
            return _error_inv(inv, f"invalid page_range format: {page_range}. Use e.g. '1-3' or '5'")
        # Validate range
        if start_page is not None and (start_page < 0 or end_page < start_page):
            return _error_inv(inv, f"invalid page_range: start must be >= 1 and <= end")
        if end_page is not None and start_page is not None and (end_page - start_page >= 100):
            return _error_inv(inv, f"page_range too large: max 100 pages. Got {end_page - start_page + 1}")

    # ── Try to import PyPDF2 ──
    try:
        import PyPDF2  # noqa: F811
        _method = "pypdf2"
    except ImportError:
        try:
            import pypdf
            PyPDF2 = pypdf
            _method = "pypdf2"
        except ImportError:
            # No PyPDF2 available — return dependency missing, no text fallback
            return _result(False, {
                "ok": False,
                "error": "pdf dependency missing (PyPDF2 not installed)",
                "file_size": file_size,
            })

    try:
        reader = PyPDF2.PdfReader(str(target))
        page_count = len(reader.pages)

        # Determine page range
        if start_page is not None:
            pages_to_read = range(max(0, start_page), min(page_count, end_page + 1))
        else:
            pages_to_read = range(page_count)

        text_parts = []
        for i in pages_to_read:
            try:
                page_text = reader.pages[i].extract_text()
                if page_text:
                    text_parts.append(page_text)
            except Exception:
                text_parts.append(f"[page {i + 1} extraction failed]")

        full_text = "\n\n".join(text_parts)

        return _result(inv, True, {
            "text": full_text,
            "page_count": page_count,
            "pages_read": len(text_parts),
            "file_size": file_size,
            "char_count": len(full_text),
            "method": _method,
        })
    except Exception as e:
        return _error_inv(inv, str(e)[:200])

__all__ = ['handle_pdf_extract_text']
