from typing import cast

from slack_bolt import App
from slack_sdk.errors import SlackApiError
from slack_sdk.http_retry import RateLimitErrorRetryHandler

import store
from common import LOG
from edstem.models import ThreadSummary
from edstem.stream import StreamThread
from edstem_client import EDSTEM_COURSE_ID
from env import get_env_variable

app = App(token=get_env_variable("SLACK_BOT_TOKEN"))
app.client.retry_handlers.append(RateLimitErrorRetryHandler(max_retry_count=3))

SLACK_CHANNEL_ID = get_env_variable('SLACK_CHANNEL_ID')

ASK_FOR_HELP_ACTION_ID = 'ask_for_help'
UMICH_EMAIL_DOMAIN = "@umich.edu"

# uniqname -> slack user id, populated at startup to prevent rate limiting
USER_IDS: dict[str, str] = {}

def mention_user_by_uniqname(uniqname: str) -> str:
    return f"<@{get_user_id_by_uniqname(uniqname)}>"

def load_user_ids() -> None:
    """Cache every workspace member's uniqname by paging through users.list once."""
    cursor = ""

    while True:
        page = app.client.users_list(limit=200, cursor=cursor)

        for member in page["members"]:
            email = member.get("profile", {}).get("email") or ""

            if email.endswith(UMICH_EMAIL_DOMAIN):
                USER_IDS[email[: -len(UMICH_EMAIL_DOMAIN)]] = member["id"]

        cursor = page.get("response_metadata", {}).get("next_cursor") or ""

        if not cursor:
            break

    LOG.info(f"cached {len(USER_IDS)} slack user ids")

def get_user_id_by_uniqname(uniqname: str) -> str:
    # a miss means someone joined the workspace after startup, so look them up
    # once and keep the answer for the rest of the run
    if uniqname not in USER_IDS:
        USER_IDS[uniqname] = app.client.users_lookupByEmail(
            email=uniqname + UMICH_EMAIL_DOMAIN
        )["user"]["id"]

    return USER_IDS[uniqname]

def build_post_url(course_id: int, question_id: int, question_number: int) -> str:
    return f"<https://edstem.org/us/courses/{course_id}/discussion/{question_id}|{question_number}>"


def build_new_post_message(course_id: int, event_thread: StreamThread | ThreadSummary, user: str) -> str:
    if event_thread.type == "post":
        return (
        f"""New Edstem post: {build_post_url(course_id, event_thread.id, event_thread.number)}.
Note: if it is a post that should be a question, please turn the post into a question via Edit -> Question -> Save.
Assignee: {mention_user_by_uniqname(user)}"""
        )
    return (
    f"""New Edstem question: {build_post_url(course_id, event_thread.id, event_thread.number)}.
Assignee: {mention_user_by_uniqname(user)}"""
    )


def build_help_button_block(thread_id: int, dri: str) -> dict:
    return {
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Ask for extra help"},
                "action_id": ASK_FOR_HELP_ACTION_ID,
                # the thread id travels with the button so the handler still knows
                # which question it is when the button sits on a threaded reply
                "value": f"{thread_id}:{dri}",
            }
        ],
    }

def add_reaction(channel_id: str, message_ts: str, name: str) -> None:
    try:
        app.client.reactions_add(channel=channel_id, timestamp=message_ts, name=name)
    except SlackApiError as error:
        # this is not an exceptional case
        # can happen when there's 2 staff answers, and the "accepted" answer changes,
        # edward tries to re-apply the check reaction
        if error.response.get("error") != "already_reacted":
            raise

        LOG.info(f"reaction {name} was already on message {message_ts}")


def remove_reaction(channel_id: str, message_ts: str, name: str) -> None:
    try:
        app.client.reactions_remove(channel=channel_id, timestamp=message_ts, name=name)
    except SlackApiError as error:
        if error.response.get("error") != "no_reaction":
            raise

        LOG.info(f"reaction {name} was not on message {message_ts}")


def post_question(thread: StreamThread | ThreadSummary, dri: str) -> None:
    """Announce a thread in slack and record it so updates can find it later."""
    message = build_new_post_message(EDSTEM_COURSE_ID, thread, dri)

    posted = app.client.chat_postMessage(
        channel=SLACK_CHANNEL_ID,
        text=message,
        unfurl_links=False,
        blocks=[
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": message},
            },
            build_help_button_block(thread.id, dri),
        ],
    )

    store.add_question(
        thread_id=thread.id,
        number=thread.number,
        channel_id=SLACK_CHANNEL_ID,
        message_ts=cast(str, posted["ts"]),
        message_text=message,
        dri=dri,
    )


