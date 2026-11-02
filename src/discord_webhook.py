import json
import urllib.error
import urllib.request

import store
from common import LOG
from edstem.models import Thread
from edstem.stream import StreamThread
from edstem_client import EDSTEM_COURSE_ID
from env import get_optional_env_variable

DISCORD_WEBHOOK_URL = get_optional_env_variable("DISCORD_WEBHOOK_URL")

if DISCORD_WEBHOOK_URL is None:
    LOG.info("DISCORD_WEBHOOK_URL not set, not forwarding announcements to Discord")

# discord's limits on embed fields
MAX_TITLE_LENGTH = 256
MAX_DESCRIPTION_LENGTH = 4096

REQUEST_TIMEOUT_SECONDS = 10

# discord rejects urllib's default user agent
USER_AGENT = "Edward"


def truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def build_announcement_payload(thread: StreamThread | Thread) -> dict:
    return {
        "embeds": [
            {
                "title": truncate(thread.title, MAX_TITLE_LENGTH),
                "url": f"https://edstem.org/us/courses/{EDSTEM_COURSE_ID}/discussion/{thread.id}",
                "description": truncate(thread.document, MAX_DESCRIPTION_LENGTH),
                "footer": {"text": thread.category},
                "timestamp": thread.created_at,
            }
        ],
    }


def post_announcement(thread: StreamThread | Thread) -> None:
    """Forward an edstem announcement to discord, once per thread."""
    assert thread.type == "announcement", f"thread {thread.id} is not an announcement"

    if DISCORD_WEBHOOK_URL is None:
        return

    if thread.is_private:
        LOG.info(f"not forwarding private announcement {thread.id} to discord")
        return

    if store.is_announcement_sent(thread.id):
        return

    request = urllib.request.Request(
        DISCORD_WEBHOOK_URL,
        data=json.dumps(build_announcement_payload(thread)).encode(),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )

    try:
        urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS).close()
    except urllib.error.HTTPError as error:
        LOG.warning(
            f"discord rejected announcement {thread.id}: {error.code} {error.read().decode(errors='replace')}"
        )
        return
    except (urllib.error.URLError, TimeoutError) as error:
        LOG.warning(f"could not send announcement {thread.id} to discord: {error}")
        return

    store.add_sent_announcement(thread.id)

    LOG.info(f"forwarded announcement {thread.id} to discord")
