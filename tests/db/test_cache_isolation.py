"""Regression tests for the cache-scoping bug: source_documents used to key its cache
purely on content_hash, so identical text submitted to two different projects would
incorrectly return the first project's cached result for the second. Fixed by scoping the
UNIQUE constraint (and every read/write) to (content_hash, project_id)."""
import hashlib

from apps.db.db import cache_pipeline_result, get_cached_pipeline_result


def test_two_projects_with_identical_text_dont_collide(test_project):
    other_project = f"{test_project}-other"
    text = "cache isolation test content - unique marker for this test run"
    content_hash = hashlib.sha256(text.encode()).hexdigest()

    try:
        cache_pipeline_result(text, {"tasks_count": 3, "note": "belongs to test_project"}, test_project)

        result_for_owner = get_cached_pipeline_result(content_hash, test_project)
        result_for_other = get_cached_pipeline_result(content_hash, other_project)

        assert result_for_owner is not None
        assert result_for_owner["note"] == "belongs to test_project"
        assert result_for_other is None, "a different project must never see another project's cached result"
    finally:
        # test_project fixture only tears down `test_project` itself - this second
        # synthetic id needs its own cleanup since it was never yielded by the fixture.
        from apps.db.db import get_connection
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM source_documents WHERE project_id = %s", (other_project,))
        conn.commit()
        cur.close()
        conn.close()


def test_same_project_resubmission_still_hits_cache(test_project):
    text = "same-project cache hit regression check"
    content_hash = hashlib.sha256(text.encode()).hexdigest()

    cache_pipeline_result(text, {"tasks_count": 1}, test_project)
    result = get_cached_pipeline_result(content_hash, test_project)

    assert result == {"tasks_count": 1}
