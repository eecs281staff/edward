import csv
from typing import Literal, cast, get_args

DayOfWeek = Literal["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]

class Parser:
    def parse_csv(self, filename: str) -> dict[DayOfWeek, list[str]]:
        days = get_args(DayOfWeek)
        with open(filename, newline="", encoding="utf-8") as file:
            rows = list(csv.reader(file))
        if not rows:
            return {}
        columns: dict[DayOfWeek, int] = {}
        for index, cell in enumerate(rows[0]):
            day = cell.strip()
            if day in days and day not in columns:
                if day not in DayOfWeek.__args__:
                    print(f"warn: invalid day: {day}")
                    continue

                columns[cast(DayOfWeek, day)] = index
        schedule: dict[DayOfWeek, list[str]] = {day: [] for day in columns}
        # skip first row which just has the days of the week
        for row in rows[1:]:
            names = {
                day: row[index].strip() if index < len(row) else ""
                for day, index in columns.items()
            }
            if not any(names.values()):
                break
            for day, name in names.items():
                if name:
                    schedule[cast(DayOfWeek, day)].append(name)
        return schedule
