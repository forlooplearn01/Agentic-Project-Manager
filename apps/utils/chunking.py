# Splits document text into small, overlapping windows for embedding + semantic retrieval.
# Deliberately separate from apps/nodes/extract.py's own _chunk_text(): that one uses
# 15,000-char windows sized to fit the extraction LLM's prompt budget, which is far too
# coarse for retrieval - a single embedding representing ~3,000 words can't distinguish
# "the sentence about payment terms" from "the sentence about the timeline." This uses the
# same sentence-boundary-aware technique at a granularity tuned for precise semantic search.

CHUNK_SIZE = 1000
# ~15% overlap - enough that a fact split across a chunk boundary still appears whole in at
# least one chunk, without ballooning the number of chunks per document.
CHUNK_OVERLAP = 150


def chunk_document(text: str) -> list:
    text = (text or "").strip()
    if not text:
        return []

    chunks = []
    start = 0
    while start < len(text):
        end = start + CHUNK_SIZE
        if end < len(text):
            # Prefer breaking at a sentence boundary within the last 100 chars of the
            # window so a fact isn't split mid-sentence.
            boundary = text.rfind(".", end - 100, end)
            if boundary == -1:
                boundary = text.rfind("\n", end - 100, end)
            if boundary != -1:
                end = boundary + 1
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        next_start = end - CHUNK_OVERLAP
        # Guard against a zero/negative-progress loop on pathological input (e.g. a huge
        # run of whitespace that makes end == start after stripping).
        start = next_start if next_start > start else end

    return chunks
