import random
from datetime import datetime

import store
from common import LOG, TIMEZONE, get_current_day
from csvparser import DayOfWeek, Parser
from edstem.models import decode
from edstem.stream import StreamServerMessage, StreamThreadNew
from edstem_client import EDSTEM_COURSE_ID, ed_client
from slack_app import (
    UMICH_EMAIL_DOMAIN,
    add_reaction,
    app,
    build_help_button_block,
    mention_user_by_uniqname,
    post_question,
    remove_reaction,
)

STAFF_CSV_FILE = "staff.csv"
BACKFILL_THREAD_LIMIT = 50

class QuestionAssigner:
    # if on, will skip all posts and questions created by admins,
    # making testing a bit harder
    # also prevents staff from "gaming" the system
    # turned off with the --include-staff-posts flag for easier testing
    skip_staff_posts: bool = True

    # map from Edstem user id to uniqname
    staff_uniqnames_by_id: dict[int, str] = {}

    # map from day of week to staff uniqnames responsible for that day
    day_to_responsible_staff_uniqnames: dict[DayOfWeek, list[str]] = {}

    # set of staff uniqnames
    all_staff_uniqnames: set = set()

    # permutation of all_staff_uniqnames that extra help pulls from
    # ensures repeats happens only after all staff have been pinged
    staff_helper_pool_uniqnames: list[str] = []

    # initialize static vars, idk how to do this in python better
    @staticmethod
    def initialize() -> None:
        QuestionAssigner.staff_uniqnames_by_id.update(
            {
                user.user_id: user.email.removesuffix(UMICH_EMAIL_DOMAIN)
                for user in ed_client.get_course_users(EDSTEM_COURSE_ID).unwrap()
                if user.course_role != "student"
            }
        )
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
        requester: str | None = None,
    ) -> None:
        """Pull in extra staff for a question and retire its help button.

        ``parent_ts`` is the original question message (where the reply goes);
        ``button_ts`` is whichever message the button was actually on, which is a
        threaded reply when help is requested after an answer was un-accepted.

        ``requester`` is the uniqname of whoever pressed the button, which is not
        the assignee when a non-assignee confirmed the request.
        """
        current_day = get_current_day()

        # neither the assignee nor whoever asked can be their own extra help
        excluded = {dri} if requester is None else {dri, requester}

        helpers = [staff for staff in QuestionAssigner.day_to_responsible_staff_uniqnames.get(current_day, []) if staff not in excluded]

        if len(helpers) < 3:
            helpers += QuestionAssigner.take_random_helpers(3 - len(helpers), set(helpers) | excluded)

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

            if thread.user_id in QuestionAssigner.staff_uniqnames_by_id and QuestionAssigner.skip_staff_posts:
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
    def backfill_answers() -> None:
        """
        Fills in the first staff answerer, the time of that answer, and the
        acceptance. The answer time is not stored anywhere locally, so it comes
        from the created_at of the earliest staff answer on the edstem thread.
        """
        missing = set(store.get_thread_ids_missing_answer_info())

        if not missing:
            return

        result = ed_client.list_course_threads(
            EDSTEM_COURSE_ID, limit=BACKFILL_THREAD_LIMIT, sort="new"
        )

        if result.is_err():
            LOG.warning(
                f"skipping answer backfill, could not list threads: {result.unwrap_err()}"
            )
            return

        candidates = [
            thread.id
            for thread in result.unwrap().threads
            if thread.is_answered and thread.id in missing
        ]

        if not candidates:
            return

        LOG.info(f"backfilling answers for {len(candidates)} thread(s)")

        filled = 0

        for thread_id in candidates:
            detail = ed_client.get_thread(thread_id)

            if detail.is_err():
                LOG.warning(f"could not load thread {thread_id}: {detail.unwrap_err()}")
                continue

            thread = detail.unwrap().thread

            staff_answers = []

            for answer in thread.answers:
                if answer.user_id not in QuestionAssigner.staff_uniqnames_by_id:
                    continue

                try:
                    created_at = datetime.fromisoformat(answer.created_at)
                except ValueError:
                    LOG.warning(
                        f"skipping answer {answer.id} on thread {thread_id}: "
                        f"unreadable created_at {answer.created_at}"
                    )
                    continue

                staff_answers.append((created_at, answer.user_id))

            # is_answered is also true when only students answered
            if not staff_answers:
                LOG.info(f"no staff answer on thread {thread_id} to take a time from")
                continue

            answered_at, user_id = min(staff_answers)

            if store.set_answer(
                thread_id,
                QuestionAssigner.staff_uniqnames_by_id[user_id],
                answered_at.astimezone(TIMEZONE).isoformat(),
                thread.accepted_id,
            ):
                filled += 1

        LOG.info(f"backfilled answers for {filled} thread(s)")

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

        if event_thread.user_id in QuestionAssigner.staff_uniqnames_by_id and QuestionAssigner.skip_staff_posts:
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

            add_reaction(channel_id, message_ts, "white_check_mark")

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

            remove_reaction(channel_id, message_ts, "white_check_mark")

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


    @staticmethod
    def handle_thread_delete(response: StreamServerMessage) -> None:
        """Mark the slack message of a deleted Edstem thread."""
        thread_id = response.data.get("thread_id")

        if thread_id is None:
            return

        question = store.get_question(thread_id)

        if question is None:
            LOG.info(f"skipping delete for untracked thread id {thread_id}")
            return

        add_reaction(question["channel_id"], question["message_ts"], "wastebasket")

        app.client.chat_update(
            channel=question["channel_id"],
            ts=question["message_ts"],
            text=question["message_text"],
            blocks=[
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": question["message_text"]},
                }
            ],
        )

    @staticmethod
    def handle_new_comment(response: StreamServerMessage) -> None:
        """Record who gave the first staff answer on a question we track."""
        comment = response.data.get("comment", {})

        thread_id = comment.get("thread_id")

        if thread_id is None:
            return

        question = store.get_question(thread_id)

        if question is None:
            LOG.info(f"skipping comment on untracked thread id {thread_id}")
            return

        if comment.get("type") != "answer":
            return

        uniqname = QuestionAssigner.staff_uniqnames_by_id.get(comment.get("user_id"))

        if uniqname is None:
            return

        # only the first staff answer is kept
        if question["staff_answerer"] is not None:
            return

        store.set_staff_answerer(thread_id, uniqname)

        LOG.info(f"thread {thread_id} was answered by {uniqname}")
