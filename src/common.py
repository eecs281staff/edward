import logging
from datetime import datetime
from typing import cast
from zoneinfo import ZoneInfo

from csvparser import DayOfWeek

logging.basicConfig(level=logging.INFO)
LOG = logging.getLogger("EdwardLogger")


# testing flag: set to a day name (e.g. "Wednesday") to pretend that is today,
# so you can exercise a specific day's staff list. None means use the real date.
TEST_DAY: DayOfWeek | None = None

def get_current_day() -> DayOfWeek:
    if TEST_DAY is not None:
        return TEST_DAY
    return cast(DayOfWeek, datetime.now(ZoneInfo("America/New_York")).strftime("%A"))

