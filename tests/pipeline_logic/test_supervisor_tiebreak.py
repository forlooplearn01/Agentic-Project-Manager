"""_llm_tiebreak is the one conditional LLM call in the Supervisor, reached only when the
cheap deterministic signal (existing task count) conflicts with a lexical signal (SOW
markers) in the document. Must correctly pass through a valid classification, and must
degrade to a safe default ("mom") rather than raise if the model's response is unusable -
an unhandled exception here would take down the whole pipeline run over what's meant to be
a minor tie-break."""
from apps.supervisor import _llm_tiebreak
from tests.conftest import make_final_json_response, make_text_response


def test_valid_json_classification_passes_through(scripted_llm):
    scripted_llm([make_final_json_response({"document_type": "sow"})], target="apps.supervisor.complete")

    assert _llm_tiebreak("some document text") == "sow"


def test_mom_classification_passes_through(scripted_llm):
    scripted_llm([make_final_json_response({"document_type": "mom"})], target="apps.supervisor.complete")

    assert _llm_tiebreak("some document text") == "mom"


def test_unparseable_response_defaults_to_mom_not_a_crash(scripted_llm):
    scripted_llm([make_text_response("not valid json at all")], target="apps.supervisor.complete")

    assert _llm_tiebreak("some document text") == "mom"


def test_unexpected_document_type_value_defaults_to_mom(scripted_llm):
    # A model returning valid JSON but a value outside the two allowed types - must not
    # be trusted blindly.
    scripted_llm([make_final_json_response({"document_type": "invoice"})], target="apps.supervisor.complete")

    assert _llm_tiebreak("some document text") == "mom"
