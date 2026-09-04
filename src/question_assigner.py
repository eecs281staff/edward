import random

import store
from common import LOG, get_current_day
from csvparser import DayOfWeek, Parser
from edstem.models import decode
from edstem.stream import StreamServerMessage, StreamThreadNew
from edstem_client import EDSTEM_COURSE_ID, ed_client
from slack_app import app, build_help_button_block, mention_user_by_uniqname, post_question

STAFF_CSV_FILE = "staff.csv"
BACKFILL_THREAD_LIMIT = 50

# feature flag enabled/disabled for easier testing
# if on, will skip all posts and questions created by admins,
# making testing a bit harder
SKIP_STAFF_POSTS = False

class QuestionAssigner:
    # list of Edstem staff IDs
    staff_ids: list[int] = [] #= staff_ids

    # map from day of week to staff uniqnames responsible for that day
    day_to_responsible_staff_uniqnames: dict[DayOfWeek, list[str]] = {}#= day_to_responsible_staff_uniqnames

    # set of staff uniqnames
    all_staff_uniqnames: set = set() #= {staff for sublist in day_to_responsible_staff_uniqnames.values() for staff in sublist}

    # permutation of all_staff_uniqnames that extra help pulls from
    # ensures repeats happens only after all staff have been pinged
    staff_helper_pool_uniqnames: list[str] = []

    # initialize static vars, idk how to do this in python better
    @staticmethod
    def initialize() -> None:
        QuestionAssigner.staff_ids.extend([user.user_id for user in ed_client.get_course_users(EDSTEM_COURSE_ID).unwrap() if user.course_role != "student"])
        QuestionAssigner.day_to_responsible_staff_uniqnames.update(Parser().parse_csv(STAFF_CSV_FILE))
        QuestionAssigner.all_staff_uniqnames.update(staff for sublist in QuestionAssigner.day_to_responsible_staff_uniqnames.values() for staff in sublist)

    @staticmethod
    def take_next_dri() -> str:
        current_day = get_current_day()

        dri = QuestionAssigner.day_to_responsible_staff_uniqnames[current_day][0]
        QuestionAssigner.day_to_responsible_staff_uniqnames[current_day] = (
            QuestionAssigner.day_to_responsible_staff_uniqnames[current_day][1:] + [dri]
        )

        return dri


    @staticmethod
    def request_extra_help(
        thread_id: int,
        dri: str,
        channel_id: str,
        parent_ts: str,
        button_ts: str,
        button_blocks: list[dict],
        button_text: str,
        context_text: str,
    ) -> None:
        """Pull in extra staff for a question and retire its help button.

        ``parent_ts`` is the original question message (where the reply goes);
        ``button_ts`` is whichever message the button was actually on, which is a
        threaded reply when help is requested after an answer was un-accepted.
        """
        current_day = get_current_day()

        helpers = [staff for staff in QuestionAssigner.day_to_responsible_staff_uniqnames.get(current_day, []) if staff != dri]

        if len(helpers) < 3:
            helpers += QuestionAssigner.take_random_helpers(3 - len(helpers), set(helpers) | {dri})

        if not helpers:
            # LOG.warning("no staff available to ask for help")
            return

        mentions = ", ".join(mention_user_by_uniqname(helper) for helper in helpers)

        app.client.chat_postMessage(
            channel=channel_id,
            thread_ts=parent_ts,
            text=f"Requesting extra help for this question!\nAdditional assignees: {mentions}",
            unfurl_links=False,
        )

        store.set_status(thread_id, store.HELP_REQUESTED)

        # drop the button so help can only be requested once per message
        blocks = [block for block in button_blocks if block["type"] != "actions"]
        blocks.append(
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": context_text}],
            }
        )

        app.client.chat_update(
            channel=channel_id,
            ts=button_ts,
            text=button_text,
            blocks=blocks,
        )


    @staticmethod
    def take_random_helpers(count: int, exclude: set[str]) -> list[str]:
        """Deal ``count`` staff off the helper deck, skipping anybody in ``exclude``.

        Randomly selects a permutation of staff, doesn't give repeat staff until everyone
        is chosen
        """
        eligible = QuestionAssigner.all_staff_uniqnames - exclude
        count = min(count, len(eligible))

        picked: list[str] = []
        skipped: list[str] = []

        while len(picked) < count:
            if not QuestionAssigner.staff_helper_pool_uniqnames:
                QuestionAssigner.staff_helper_pool_uniqnames.extend(QuestionAssigner.all_staff_uniqnames)
                random.shuffle(QuestionAssigner.staff_helper_pool_uniqnames)

            staff = QuestionAssigner.staff_helper_pool_uniqnames.pop(0)

            if staff in exclude or staff in picked:
                skipped.append(staff)
            else:
                picked.append(staff)

        QuestionAssigner.staff_helper_pool_uniqnames[:0] = skipped

        return picked

    @staticmethod
    def backfill_missed_questions() -> None:
        """Post questions that came in while the bot was not listening.

        Fetch all posts and create Slack threads for questions we missed.
        """
        last_number = store.get_max_number()

        if last_number is None:
            LOG.info("nothing stored yet, so there is nothing to catch up on")
            return

        result = ed_client.list_course_threads(
            EDSTEM_COURSE_ID, limit=BACKFILL_THREAD_LIMIT, sort="new"
        )

        if result.is_err():
            LOG.warning(f"skipping catch up, could not list threads: {result.unwrap_err()}")
            return

        # oldest first, so the slack messages and the dri rotation stay in order
        missed = sorted(
            (thread for thread in result.unwrap().threads if thread.number > last_number),
            key=lambda thread: thread.number,
        )

        LOG.info(f"catching up on {len(missed)} thread(s) newer than number {last_number}")

        for thread in missed:
            if thread.type == "announcement":
                continue

            if thread.user_id in QuestionAssigner.staff_ids and SKIP_STAFF_POSTS:
                continue

            # someone answers quickly
            if store.get_question(thread.id) is not None:
                continue

            detail = ed_client.get_thread(thread.id)

            if detail.is_err():
                LOG.warning(f"could not load thread {thread.id}: {detail.unwrap_err()}")
                continue

            if detail.unwrap().thread.accepted_id is not None:
                LOG.info(f"skipping missed thread {thread.number}, it was already answered")
                continue

            LOG.info(f"posting missed thread {thread.number}")

            post_question(thread, QuestionAssigner.take_next_dri())

    @staticmethod
    def handle_new_thread(response: StreamServerMessage) -> None:
        event: StreamThreadNew = decode(StreamThreadNew, response.data)

        event_thread = event.thread

        if event_thread.course_id != EDSTEM_COURSE_ID:
            LOG.warning(f"Received message for course id {event_thread.course_id}")
            return

        if event_thread.type == "announcement":
            LOG.info(f"skipping post id {event_thread.id} because it is an announcement")
            return

        if event_thread.user_id in QuestionAssigner.staff_ids and SKIP_STAFF_POSTS:
            LOG.info(f"skipping post id {event_thread.id} because it was posted by a staff/admin\n")
            return

        post_question(event_thread, QuestionAssigner.take_next_dri())




    @staticmethod
    def handle_thread_update(response: StreamServerMessage) -> None:
        thread = response.data.get("thread", {})

        thread_id = thread.get("id")

        if thread_id is None:
            return

        question = store.get_question(thread_id)

        if question is None:
            LOG.info(f"skipping update for untracked thread id {thread_id}")
            return

        # view/reply counters come through as thread.update too, so only the frames
        # that actually carry accepted_id are interesting
        if "accepted_id" not in thread:
            return

        accepted_id = thread["accepted_id"]

        # a plain edit re-sends the whole thread with an unchanged accepted_id, so
        # only act when the value actually transitions
        if accepted_id == question["accepted_id"]:
            return

        channel_id = question["channel_id"]
        message_ts = question["message_ts"]

        if accepted_id is not None:
            store.set_status(thread_id, store.ANSWERED)

            app.client.reactions_add(
                channel=channel_id,
                timestamp=message_ts,
                name="white_check_mark",
            )

            app.client.chat_update(
                channel=channel_id,
                ts=message_ts,
                text=question["message_text"],
                blocks=[
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": question["message_text"]},
                    }
                ],
            )
        else:
            store.set_status(thread_id, store.ANSWER_NOT_ACCEPTED)

            app.client.reactions_remove(
                channel=channel_id,
                timestamp=message_ts,
                name="white_check_mark",
            )

            dri = question["dri"]

            reply = f"The accepted answer for this question was removed. {mention_user_by_uniqname(dri)}"

            app.client.chat_postMessage(
                channel=channel_id,
                thread_ts=message_ts,
                text=reply,
                unfurl_links=False,
                blocks=[
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": reply},
                    },
                    build_help_button_block(thread_id, dri),
                ],
            )

        store.set_accepted_id(thread_id, accepted_id)




