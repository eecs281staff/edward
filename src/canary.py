import threading
import time
from datetime import datetime

from common import LOG, TIMEZONE, format_duration
from edstem_client import EDSTEM_COURSE_ID, ed_client
from env import get_env_variable
from slack_app import app
from version import __version__

CANARY_CHANNEL_ID = get_env_variable("SLACK_CANARY_CHANNEL_ID")

# seconds between health checks
CHECK_INTERVAL = 60.0

# how many checks pass between edstem api probes
API_PROBE_EVERY = 5

# how long the stream may go without a frame before it counts as down, in
# seconds.
MAX_FRAME_AGE = 3 * 60 * 60.0

# how long the stream may fail to reconnect before it counts as down, in
# seconds.
MAX_STREAM_DOWN = 300.0

_started_at = time.monotonic()

_last_frame_at: float | None = None

_reconnect_count = 0

# when the stream first failed to read, None while it is up
_stream_down_since: float | None = None

# the stream thread, so the canary can notice it die
_stream_thread: threading.Thread | None = None

# result of the most recent api probe
_api_problem: str | None = None

_was_healthy = True


def note_frame() -> None:
    global _last_frame_at

    _last_frame_at = time.monotonic()


def note_reconnect() -> None:
    global _reconnect_count

    _reconnect_count += 1


def note_stream_down() -> None:
    global _stream_down_since

    if _stream_down_since is None:
        _stream_down_since = time.monotonic()


def note_stream_up() -> None:
    global _stream_down_since

    _stream_down_since = None


def status_line() -> str:
    if _last_frame_at is None:
        last_frame = "no Edstem frames yet"
    else:
        last_frame = (
            f"last Edstem frame {format_duration(time.monotonic() - _last_frame_at)} ago"
        )

    return (
        f"Edward v{__version__} | up {format_duration(time.monotonic() - _started_at)}"
        f" | {last_frame} | {_reconnect_count} stream reconnects"
    )


def probe_api() -> str | None:
    result = ed_client.list_course_threads(EDSTEM_COURSE_ID, limit=1)

    if result.is_err():
        return f"the Edstem API is not answering: {result.unwrap_err()}"

    return None


def find_problems() -> list[str]:
    problems = []

    if _stream_thread is not None and not _stream_thread.is_alive():
        problems.append("the Edstem stream thread stopped running")

    if _stream_down_since is not None:
        down_for = time.monotonic() - _stream_down_since

        if down_for >= MAX_STREAM_DOWN:
            problems.append(
                f"cannot reach the Edstem stream, down {format_duration(down_for)},"
                " new Edstem questions are not being assigned"
            )

    # before the first frame there is nothing to measure against but startup
    if _last_frame_at is None:
        age = time.monotonic() - _started_at

        if age >= MAX_FRAME_AGE:
            problems.append(f"no Edstem frames since starting {format_duration(age)} ago")
    else:
        age = time.monotonic() - _last_frame_at

        if age >= MAX_FRAME_AGE:
            problems.append(f"no Edstem frames for {format_duration(age)}")

    if _api_problem is not None:
        problems.append(_api_problem)

    return problems


def post_heartbeat(problems: list[str]) -> None:
    state = "; ".join(problems) if problems else "healthy"

    text = f"{status_line()} | {state} | as of {datetime.now(TIMEZONE).strftime('%H:%M')}"

    app.client.chat_postMessage(channel=CANARY_CHANNEL_ID, text=text)


def report_change(problems: list[str]) -> None:
    if problems:
        text = "<!channel> Edward is unhealthy:\n" + "\n".join(
            f"- {problem}" for problem in problems
        )
    else:
        text = "Edward is healthy again."

    app.client.chat_postMessage(channel=CANARY_CHANNEL_ID, text=text)


def run() -> None:
    global _api_problem, _was_healthy

    checks = 0

    while True:
        if checks % API_PROBE_EVERY == 0:
            _api_problem = probe_api()

        problems = find_problems()

        healthy = not problems

        try:
            if healthy != _was_healthy:
                report_change(problems)

                _was_healthy = healthy

            post_heartbeat(problems)
        except Exception as exception:
            # slack being unreachable must not kill the canary
            LOG.warning(f"could not post the heartbeat: {exception}")

        checks += 1

        time.sleep(CHECK_INTERVAL)


def start(stream_thread: threading.Thread) -> None:
    global _stream_thread

    _stream_thread = stream_thread

    threading.Thread(target=run, daemon=True).start()
