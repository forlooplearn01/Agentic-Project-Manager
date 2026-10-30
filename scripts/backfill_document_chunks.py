"""Gives every stored SOW / minutes document that has no search chunks its chunks, so AI Chat can answer questions about what the
document says (go-live dates, user counts, terms). Safe to re-run: documents that already have chunks are skipped.

    docker compose exec api python -m scripts.backfill_document_chunks"""
from apps.db.db import backfill_missing_document_chunks

if __name__ == "__main__":
    print(f"documents filled: {backfill_missing_document_chunks()}")
