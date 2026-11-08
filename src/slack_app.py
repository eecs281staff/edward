from typing import cast

from slack_bolt import App
from slack_sdk.errors import SlackApiError
from slack_sdk.http_retry import RateLimitErrorRetryHandler

import store
from common import LOG
from edstem.models import ThreadSummary
from edstem.stream import StreamThread
from env import settings

app = App(token=settings.SLACK_BOT_TOKEN)
app.client.retry_handlers.append(RateLimitErrorRetryHandler(max_retry_count=3))

ASK_FOR_HELP_ACTION_ID = 'ask_for_help'
ASK_FOR_HELP_CONFIRM_CALLBACK_ID = 'confirm_ask_for_help'
ESCALATE_TO_FACULTY_ACTION_ID = 'escalate_to_faculty'
ESCALATE_TO_FACULTY_CONFIRM_CALLBACK_ID = 'confirm_escalate_to_faculty'
UMICH_EMAIL_DOMAIN = "@umich.edu"

NEW_POST_PREFIX = "New Edstem post:"
NEW_QUESTION_PREFIX = "New Edstem question:"

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

def check_faculty_user_ids() -> None:
    """Check if all faculty uniqnames are in the Slack workspaces"""
    for uniqname in settings.FACULTY_UNIQNAMES:
        try:
            get_user_id_by_uniqname(uniqname)
        except SlackApiError as error:
            raise ValueError(f"faculty uniqname {uniqname} is not in the slack workspace") from error

def get_user_id_by_uniqname(uniqname: str) -> str:
    # a miss means someone joined the workspace after startup, so look them up
    # once and keep the answer for the rest of the run
    if uniqname not in USER_IDS:
        USER_IDS[uniqname] = app.client.users_lookupByEmail(
            email=uniqname + UMICH_EMAIL_DOMAIN
        )["user"]["id"]

    return USER_IDS[uniqname]

def get_uniqname_by_user_id(user_id: str) -> str | None:
    """Reverse of get_user_id_by_uniqname."""
    for uniqname, cached_id in USER_IDS.items():
        if cached_id == user_id:
            return uniqname

    # a miss means they joined the workspace after startup
    email = app.client.users_info(user=user_id)["user"].get("profile", {}).get("email") or ""

    if not email.endswith(UMICH_EMAIL_DOMAIN):
        return None

    uniqname = email[: -len(UMICH_EMAIL_DOMAIN)]

    USER_IDS[uniqname] = user_id

    return uniqname

def build_post_url(course_id: int, question_id: int, question_number: int) -> str:
    return f"<https://edstem.org/us/courses/{course_id}/discussion/{question_id}|{question_number}>"


def build_new_post_message(course_id: int, event_thread: StreamThread | ThreadSummary, user: str | None) -> str:
    assignee = (
        "Already has an accepted answer."
        if user is None
        else f"Assignee: {mention_user_by_uniqname(user)}"
    )

    if event_thread.type == "post":
        return (
        f"""{NEW_POST_PREFIX} {build_post_url(course_id, event_thread.id, event_thread.number)}.
Note: if it is a post that should be a question, please turn the post into a question via Edit -> Question -> Save.
{assignee}"""
        )
    return (
    f"""{NEW_QUESTION_PREFIX} {build_post_url(course_id, event_thread.id, event_thread.number)}.
{assignee}"""
    )


def build_question_buttons_block(thread_id: int, dri: str, faculty_button: bool = True) -> dict:
    value = f"{thread_id}:{dri}"

    elements = [
        {
            "type": "button",
            "text": {"type": "plain_text", "text": "Ask for extra help"},
            "action_id": ASK_FOR_HELP_ACTION_ID,
            "value": value,
        }
    ]

    if faculty_button and settings.FACULTY_UNIQNAMES:
        elements.append(
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Escalate to faculty"},
                "action_id": ESCALATE_TO_FACULTY_ACTION_ID,
                "value": value,
            }
        )

    return {"type": "actions", "elements": elements}


def remove_button(blocks: list[dict], action_id: str) -> list[dict]:
    kept = []

    for block in blocks:
        if block["type"] == "actions":
            elements = [element for element in block["elements"] if element.get("action_id") != action_id]

            if not elements:
                continue

            block = {**block, "elements": elements}

        kept.append(block)

    return kept


def build_context_block(text: str) -> dict:
    return {
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": text}],
    }


def build_escalated_context_block(user_id: str) -> dict:
    return build_context_block(f"Escalated to faculty by <@{user_id}>")

def build_help_confirm_modal(dri: str, private_metadata: str) -> dict:
    """Confirmation shown when someone who is not the assignee presses the button."""
    return {
        "type": "modal",
        "callback_id": ASK_FOR_HELP_CONFIRM_CALLBACK_ID,
        "title": {"type": "plain_text", "text": "Ask for extra help"},
        "submit": {"type": "plain_text", "text": "Request help"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "private_metadata": private_metadata,
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"This question is assigned to {mention_user_by_uniqname(dri)}, not you."
                        " Request extra help for it anyway?"
                    ),
                },
            }
        ],
    }

def build_faculty_confirm_modal(dri: str, private_metadata: str) -> dict:
    """Confirmation shown when someone who is not the assignee presses the escalate button."""
    return {
        "type": "modal",
        "callback_id": ESCALATE_TO_FACULTY_CONFIRM_CALLBACK_ID,
        "title": {"type": "plain_text", "text": "Escalate to faculty"},
        "submit": {"type": "plain_text", "text": "Escalate"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "private_metadata": private_metadata,
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"This question is assigned to {mention_user_by_uniqname(dri)}, not you."
                        " Escalate it to faculty anyway?"
                    ),
                },
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


def post_channel_message(text: str) -> None:
    """Post a plain message to the channel."""
    app.client.chat_postMessage(channel=settings.SLACK_CHANNEL_ID, text=text)


def post_question(thread: StreamThread | ThreadSummary, dri: str | None) -> str:
    """Announce a thread in slack and write to the database.

    If there already exists an accepted answer, `dri` will be None and the message
    returned won't ping anyone.
    """
    message = build_new_post_message(settings.COURSE_ID, thread, dri)

    blocks: list[dict] = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": message},
        },
    ]

    if dri is not None:
        blocks.append(build_question_buttons_block(thread.id, dri))

    posted = app.client.chat_postMessage(
        channel=settings.SLACK_CHANNEL_ID,
        text=message,
        unfurl_links=False,
        blocks=blocks,
    )

    message_ts = cast(str, posted["ts"])

    store.add_question(
        thread_id=thread.id,
        number=thread.number,
        channel_id=settings.SLACK_CHANNEL_ID,
        message_ts=message_ts,
        message_text=message,
        dri=dri or "",
    )

    return message_ts


