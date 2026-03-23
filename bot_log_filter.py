"""Helpers for filtering bot log lines in the dashboard (no Flask imports)."""


def bot_log_line_is_survivor_event(line: str) -> bool:
    """True if a log line is about survivor SL-to-cost (for dashboard filtering)."""
    low = line.lower()
    return any(
        m in low
        for m in (
            "survivor",
            "tightened to cost",
            "failed to modify survivor",
            "failed to fetch order book for survivor",
            "survivor sl order not in order book",
            "survivor sl-to-cost",
        )
    )
