import io

SUPPORTED_EXTENSIONS = {"txt", "docx", "pdf"}


class ExtractionError(Exception):
    """Raised when a file can't be read (unsupported type) or yields no usable text
    (e.g. a scanned/image-only PDF with nothing extractable)."""


def extract_text(filename: str, content: bytes) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if ext not in SUPPORTED_EXTENSIONS:
        raise ExtractionError(
            f"Unsupported file type '.{ext}'. Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )

    if ext == "txt":
        text = _extract_txt(content)
    elif ext == "docx":
        text = _extract_docx(content)
    else:
        text = _extract_pdf(content)

    text = text.strip()
    if not text:
        raise ExtractionError(
            "No extractable text found in this file - it may be empty, or (for PDFs) a "
            "scanned/image-only document with no real text layer. Try pasting the text instead."
        )
    return text


def _extract_txt(content: bytes) -> str:
    return content.decode("utf-8", errors="replace")


def _extract_docx(content: bytes) -> str:
    from docx import Document

    doc = Document(io.BytesIO(content))
    parts = [p.text for p in doc.paragraphs if p.text.strip()]

    # SOWs commonly put deliverables/pricing in tables - paragraphs alone would miss them.
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(" | ".join(cells))

    return "\n".join(parts)


def _extract_pdf(content: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(content))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n".join(pages)
