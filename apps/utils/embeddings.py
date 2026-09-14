from sentence_transformers import SentenceTransformer

# Load the embedding model once at module import time so it is shared across all calls.
# all-MiniLM-L6-v2 produces 384-dimensional vectors - matches the task_embedding column in Postgres.
model = SentenceTransformer("all-MiniLM-L6-v2")


def get_embedding(text: str) -> list:
    # Return None for empty input - the DB column allows NULL and pgvector skips NULL rows
    if not text:
        return None

    # Encode text to a 384-dim float vector, then convert to a plain Python list
    # so it can be serialized to JSON and passed to psycopg2 as a pgvector value
    embedding = model.encode(text)
    return embedding.tolist()


def get_embeddings_batch(texts: list) -> list:
    # Encode a list of texts in one model.encode() call - significantly faster than
    # calling get_embedding() in a loop because the transformer processes all texts
    # in a single forward pass rather than N separate passes.
    # Returns a list of embeddings (each a plain Python list), preserving input order.
    # Empty or None entries are returned as None so callers can handle them gracefully.
    if not texts:
        return []

    # Separate valid texts from empty ones, preserving index mapping
    results = [None] * len(texts)
    valid_indices = [i for i, t in enumerate(texts) if t and isinstance(t, str)]
    valid_texts = [texts[i] for i in valid_indices]

    if valid_texts:
        embeddings = model.encode(valid_texts)
        for idx, emb in zip(valid_indices, embeddings):
            results[idx] = emb.tolist()

    return results
