from concall.ingest.classify import classify_text


def test_transcript_positive():
    assert (
        classify_text(
            "Transcript of the earnings conference call held on 3 November 2026"
        )
        == "transcript"
    )


def test_audio_only():
    assert (
        classify_text("Audio Recording of the conference call with analysts")
        == "audio_only"
    )


def test_intimation_not_transcript():
    assert classify_text("Intimation of upcoming earnings conference call") == "intimation"


def test_board_outcome_ignored():
    assert classify_text("Outcome of Board Meeting") == "other"
