import sqlite3
import threading

DB_FILE = "../questions.db"

UNANSWERED = "unanswered"
ANSWERED = "answered"
HELP_REQUESTED = "help_requested"
ANSWER_NOT_ACCEPTED = "answer_not_accepted"

_lock = threading.Lock()
_connection = sqlite3.connect(DB_FILE, check_same_thread=False)
_connection.row_factory = sqlite3.Row


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
                accepted_id INTEGER
            )
            """
        )
        _connection.commit()


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
                (thread_id, number, channel_id, message_ts, message_text, dri, status, accepted_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (thread_id, number, channel_id, message_ts, message_text, dri, UNANSWERED),
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


def set_accepted_id(thread_id: int, accepted_id: int | None) -> None:
    with _lock:
        _connection.execute(
            "UPDATE questions SET accepted_id = ? WHERE thread_id = ?", (accepted_id, thread_id)
        )
        _connection.commit()
