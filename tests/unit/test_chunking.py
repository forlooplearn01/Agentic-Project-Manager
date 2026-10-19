"""Pure unit tests for chunk_document (apps/utils/chunking.py) - no DB, no LLM."""
from apps.utils.chunking import chunk_document, CHUNK_SIZE


def test_empty_input_returns_no_chunks():
    assert chunk_document("") == []
    assert chunk_document(None) == []
    assert chunk_document("   ") == []


def test_short_text_produces_a_single_chunk():
    text = "This is a short document that fits in one chunk easily."
    chunks = chunk_document(text)

    assert len(chunks) == 1
    assert chunks[0] == text


def test_long_text_splits_into_multiple_overlapping_chunks():
    # Well past CHUNK_SIZE, so this must split into at least 2 chunks.
    text = ("This is one sentence. " * 200).strip()
    assert len(text) > CHUNK_SIZE

    chunks = chunk_document(text)

    assert len(chunks) > 1
    # every chunk should itself be non-empty and no chunk should exceed the target
    # size by an unreasonable margin (sentence-boundary search can push it slightly over)
    for chunk in chunks:
        assert chunk.strip()
        assert len(chunk) <= CHUNK_SIZE + 200


def test_no_content_is_lost_at_chunk_boundaries():
    # A distinctive marker placed right where a naive, non-overlapping split would cut it
    # in half must still appear intact in at least one chunk.
    filler_a = "word " * 150  # pushes the marker close to a chunk boundary
    marker = "UNIQUE_BOUNDARY_MARKER_12345"
    filler_b = "word " * 150
    text = filler_a + marker + " " + filler_b

    chunks = chunk_document(text)

    assert any(marker in chunk for chunk in chunks)
