import random
import threading
import time

from slack_bolt.adapter.socket_mode import SocketModeHandler

import store
from common import LOG
from edstem.models import ParseError
from edstem.stream import Stream
from edstem_client import EDSTEM_AUTH_TOKEN, EDSTEM_COURSE_ID
from env import get_env_variable
from question_assigner import QuestionAssigner
from slack_app import (
    app,
    get_user_id_by_uniqname,
    load_user_ids,
)
from version import __version__

NEW_THREAD_TYPE = "thread.new"

THREAD_UPDATE_TYPE = "thread.update"

NEW_COMMENT_TYPE = "comment.new"


ASK_FOR_HELP_ACTION_ID = "ask_for_help"

# how long to wait before the first reconnect attempt, exponential backoff
# in seconds
INITIAL_RECONNECT_DELAY = 1.0

# seconds
MAX_RECONNECT_DELAY = 60.0

# how many of the newest threads to look at when catching up on startup
BACKFILL_THREAD_LIMIT = 50

STATUS_COMMAND = "/edward-status"

# health state, written by the stream thread and read by the slack thread
_started_at = time.monotonic()

# None until the first frame arrives, so a bot that connected but never heard
# anything does not look healthy
_last_frame_at: float | None = None

_reconnect_count = 0

@app.action(ASK_FOR_HELP_ACTION_ID)
def handle_ask_for_help(ack, body) -> None:
    ack()

    raw_thread_id, _, dri = body["actions"][0]["value"].partition(":")

    thread_id = int(raw_thread_id)

    # LOG.info(f"DRI: {dri}")

    user_id = body["user"]["id"]

    if user_id != get_user_id_by_uniqname(dri):
        app.client.chat_postEphemeral(
            channel=body["channel"]["id"],
            user=user_id,
            text="Only the assignee for this question can request help.",
        )
        return

    question = store.get_question(thread_id)

    if question is None:
        LOG.warning(f"no stored question for thread id {thread_id}")
        return

    QuestionAssigner.request_extra_help(
        thread_id=thread_id,
        dri=dri,
        channel_id=body["channel"]["id"],
        parent_ts=question["message_ts"],
        button_ts=body["message"]["ts"],
        button_blocks=body["message"]["blocks"],
        button_text=body["message"].get("text", ""),
        context_text=f"Help requested by <@{user_id}>",
    )



def format_duration(seconds: float) -> str:
    """Render a span as its two largest useful units, e.g. "3h 12m"."""
    hours, remainder = divmod(int(seconds), 3600)

    minutes, secs = divmod(remainder, 60)

    if hours:
        return f"{hours}h {minutes}m"

    if minutes:
        return f"{minutes}m {secs}s"

    return f"{secs}s"


@app.command(STATUS_COMMAND)
def handle_status(ack, respond) -> None:
    ack()

    if _last_frame_at is None:
        last_frame = "no Edstem frames yet"
    else:
        last_frame = (
            f"last Edstem frame {format_duration(time.monotonic() - _last_frame_at)} ago"
        )

    respond(
        f"Edward v{__version__} | up {format_duration(time.monotonic() - _started_at)}"
        f" | {last_frame} | {_reconnect_count} stream reconnects"
    )


def assign_staff_to_new_questions() -> None:
    global _last_frame_at, _reconnect_count

    store.init_db()
    QuestionAssigner.initialize()

    stream: Stream = Stream(EDSTEM_AUTH_TOKEN)

    res = stream.connect()
    if res.is_err():
        raise ValueError(f"Failed to connect to stream: {res.error}")

    # print(f"analytics: {ed_client.get_course_analytics_discussion(EDSTEM_COURSE_ID).unwrap()}")

    stream.subscribe_course(int(EDSTEM_COURSE_ID))


    # done after subscribing so a question arriving right now is caught either
    # by the catch up or by the stream, rather than falling between the two
    QuestionAssigner.backfill_missed_questions()

    backoff = INITIAL_RECONNECT_DELAY

    while True:
        recv = stream.receive()

        if recv.is_err():
            error = recv.unwrap_err()

            # one unreadable frame does not mean the socket is broken, so keep reading
            if isinstance(error, ParseError):
                LOG.warning(f"skipping unparseable frame: {error}")
                continue

            LOG.warning(f"stream dropped ({error}), reconnecting in {backoff:.1f}s")

            time.sleep(backoff + random.uniform(0, 1))

            backoff = min(backoff * 2, MAX_RECONNECT_DELAY)

            stream.close()

            if stream.connect().is_err():
                LOG.warning("reconnect failed")
                continue

            _reconnect_count += 1

            stream.subscribe_course(EDSTEM_COURSE_ID)

            # the stream does not replay what it sent while we were away, so pick
            # up anything that came in during the drop
            QuestionAssigner.backfill_missed_questions()

            continue

        backoff = INITIAL_RECONNECT_DELAY

        _last_frame_at = time.monotonic()

        response = recv.unwrap()

        print(f"response: {response}")

        if response.type == NEW_THREAD_TYPE:
            QuestionAssigner.handle_new_thread(response)
        elif response.type == THREAD_UPDATE_TYPE:
            QuestionAssigner.handle_thread_update(response)
        elif response.type == NEW_COMMENT_TYPE:
            QuestionAssigner.handle_new_comment(response)
        else:
            LOG.info(f"skipping received response of type {response.type}")
            continue



if __name__ == "__main__":
    load_user_ids()

    question_assigner = threading.Thread(target=assign_staff_to_new_questions)
    question_assigner.start()
    SocketModeHandler(app, get_env_variable("SLACK_APP_TOKEN")).start()
