"""Error messages shown in the UI must stay short - not a raw API exception dump."""

from pipeline import _short_error


def test_quota_error_is_summarized_not_dumped():
    raw = (
        "429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded "
        "your current quota...', 'status': 'RESOURCE_EXHAUSTED', 'details': [...]}}"
    )
    short = _short_error(Exception(raw))
    assert short == "Gemini free-tier quota exhausted for the day"
    assert len(short) < 60


def test_other_errors_are_truncated_to_first_line():
    short = _short_error(Exception("boom\nsome huge traceback-like body " + "x" * 500))
    assert short == "boom"
    assert len(short) <= 160
