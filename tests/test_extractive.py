from concall.summarize.extractive import extractive_summary


SAMPLE = """
Moderator: Ladies and gentlemen, welcome to the Q2 FY27 earnings conference call of Example Ltd on 3 November 2026.
CFO: Revenue grew 11% YoY to 4,067 crore. We expect mid-teens growth next year despite competitive pressure.
Analyst Jane: How should we think about margins in the US segment?
CFO: US margins were under pressure this quarter; we are guiding for a recovery in H2.
"""


def test_extractive_pulls_numbers_and_qa():
    s = extractive_summary(
        SAMPLE,
        {"company": "Example Ltd", "nse_symbol": "EXAM", "scrip_code": "500001", "source": "NSE"},
    )
    assert s.company == "Example Ltd"
    assert s.key_metrics
    assert s.notable_qa
    assert s.model == "extractive-fallback"
