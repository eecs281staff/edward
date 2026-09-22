import sqlite3
import threading
from datetime import datetime

from common import LOG, TIMEZONE

DB_FILE = "questions.db"

UNANSWERED = "unanswered"
ANSWERED = "answered"
HELP_REQUESTED = "help_requested"
ANSWER_NOT_ACCEPTED = "answer_not_accepted"

_lock = threading.Lock()
_connection = sqlite3.connect(DB_FILE, check_same_thread=False)
_connection.row_factory = sqlite3.Row


def _now() -> str:
    return datetime.now(TIMEZONE).isoformat()


def init_db() -> None:
    with _lock:
        _connection.execute(
            """
            CREATE TABLE IF NOT EXISTS questions (
                thread_id INTEGER PRIMARY KEY,
                number INTEGER NOT NULL,
                channel_id TEXT NOT NULL,
                message_ts TEXT NOT NULL,
                message_text TEXT NOT NULL,
                dri TEXT NOT NULL,
                status TEXT NOT NULL,
                accepted_id INTEGER,
                staff_answerer TEXT,
                assigned_at TEXT,
                answered_at TEXT
            )
            """
        )

        # databases created before these columns existed still need them
        columns = {row["name"] for row in _connection.execute("PRAGMA table_info(questions)")}

        for column in ("staff_answerer", "assigned_at", "answered_at"):
            if column not in columns:
                LOG.info(f"Adding column {column} to table questions")
                _connection.execute(f"ALTER TABLE questions ADD COLUMN {column} TEXT")

        _connection.commit()

    _backfill_assigned_at()


def _backfill_assigned_at() -> None:
    with _lock:
        rows = _connection.execute(
            "SELECT thread_id, message_ts FROM questions WHERE assigned_at IS NULL"
        ).fetchall()

        updates = []

        for row in rows:
            try:
                posted_at = datetime.fromtimestamp(float(row["message_ts"]), TIMEZONE)
            except ValueError:
                LOG.warning(
                    f"cannot backfill assigned_at for thread id {row['thread_id']}: "
                    f"unreadable message_ts {row['message_ts']}"
                )
                continue

            updates.append((posted_at.isoformat(), row["thread_id"]))

        if not updates:
            return

        _connection.executemany(
            "UPDATE questions SET assigned_at = ? WHERE thread_id = ?", updates
        )
        _connection.commit()

        LOG.info(f"backfilled assigned_at for {len(updates)} questions")


def add_question(
    thread_id: int,
    number: int,
    channel_id: str,
    message_ts: str,
    message_text: str,
    dri: str,
) -> None:
    with _lock:
        _connection.execute(
            """
            INSERT OR REPLACE INTO questions
                (thread_id, number, channel_id, message_ts, message_text, dri, status,
                 accepted_id, assigned_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)
            """,
            (thread_id, number, channel_id, message_ts, message_text, dri, UNANSWERED, _now()),
        )
        _connection.commit()


def get_max_number() -> int | None:
    """Highest thread number already posted, or None when nothing is stored yet."""
    with _lock:
        cursor = _connection.execute("SELECT MAX(number) AS number FROM questions")
        return cursor.fetchone()["number"]


def get_question(thread_id: int) -> sqlite3.Row | None:
    with _lock:
        cursor = _connection.execute(
            "SELECT * FROM questions WHERE thread_id = ?", (thread_id,)
        )
        return cursor.fetchone()


def set_status(thread_id: int, status: str) -> None:
    with _lock:
        _connection.execute(
            "UPDATE questions SET status = ? WHERE thread_id = ?", (status, thread_id)
        )
        _connection.commit()


def set_staff_answerer(thread_id: int, uniqname: str) -> None:
    """Record the first staff answer."""
    with _lock:
        _connection.execute(
            """
            UPDATE questions SET staff_answerer = ?, answered_at = ?
            WHERE thread_id = ? AND staff_answerer IS NULL
            """,
            (uniqname, _now(), thread_id),
        )
        _connection.commit()


def set_accepted_id(thread_id: int, accepted_id: int | None) -> None:
    with _lock:
        _connection.execute(
            "UPDATE questions SET accepted_id = ? WHERE thread_id = ?", (accepted_id, thread_id)
        )
        _connection.commit()
