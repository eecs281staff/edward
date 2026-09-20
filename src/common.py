import logging
from datetime import datetime
from typing import cast
from zoneinfo import ZoneInfo

from csvparser import DayOfWeek

logging.basicConfig(level=logging.INFO)
LOG = logging.getLogger("EdwardLogger")

TIMEZONE = ZoneInfo("America/New_York")


# testing flag: set to a day name (e.g. "Wednesday") to pretend that is today,
# so you can exercise a specific day's staff list. None means use the real date.
TEST_DAY: DayOfWeek | None = None

def get_current_day() -> DayOfWeek:
    if TEST_DAY is not None:
        return TEST_DAY
    return cast(DayOfWeek, datetime.now(TIMEZONE).strftime("%A"))


def format_duration(seconds: float) -> str:
    """Render a span as its two largest useful units, e.g. "3h 12m"."""
    hours, remainder = divmod(int(seconds), 3600)

    minutes, secs = divmod(remainder, 60)

    if hours:
        return f"{hours}h {minutes}m"

    if minutes:
        return f"{minutes}m {secs}s"

    return f"{secs}s"

