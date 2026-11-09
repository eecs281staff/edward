import random
import sqlite3
import threading
import time
from datetime import datetime, timedelta

import store
from common import LOG, TIMEZONE, format_duration, get_current_day
from csvparser import DayOfWeek, Parser
from discord_webhook import post_announcement
from edstem.models import HttpError, Thread, ThreadSummary, decode
from edstem.stream import StreamServerMessage, StreamThread, StreamThreadNew
from edstem_client import ed_client
from env import settings
from slack_app import (
    ASK_FOR_HELP_ACTION_ID,
    ESCALATE_TO_FACULTY_ACTION_ID,
    NEW_QUESTION_PREFIX,
    UMICH_EMAIL_DOMAIN,
    add_reaction,
    app,
    build_context_block,
    build_escalated_context_block,
    build_question_buttons_blocks,
    mention_user_by_uniqname,
    post_question,
    remove_button,
    remove_reaction,
)

STAFF_CSV_FILE = "staff.csv"

# max is always 100, it paginates on >100
THREAD_PAGE_SIZE = 100

class QuestionAssigner:
    # if on, will skip all posts and questions created by admins,
    # making testing a bit harder
    # also prevents staff from "gaming" the system
    # turned off with SKIP_STAFF_POSTS=false for easier testing
    skip_staff_posts: bool = settings.SKIP_STAFF_POSTS

    # how long a question can go without a staff answer before help is asked for
    auto_help_after_minutes: float = settings.AUTO_HELP_AFTER_MINUTES

    # older questions are left alone, so a first deploy or a long outage does not
    # ask for help on every old question at once
    auto_help_max_age_hours: float = settings.AUTO_HELP_MAX_AGE_HOURS

    # measured from the thread's created_at on Ed
    post_delay_minutes: float = settings.POST_DELAY_MINUTES

    post_lock = threading.Lock()

    dri_lock = threading.Lock()

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
                for user in ed_client.get_course_users(settings.COURSE_ID).unwrap()
                if user.course_role != "student"
            }
        )
        QuestionAssigner.day_to_responsible_staff_uniqnames.update(Parser().parse_csv(STAFF_CSV_FILE))
        QuestionAssigner.all_staff_uniqnames.update(staff for sublist in QuestionAssigner.day_to_responsible_staff_uniqnames.values() for staff in sublist)

    @staticmethod
    def can_request_extra_help() -> bool:
        """Whether there is anyone besides the assignee to pull in as extra help."""
        return len(QuestionAssigner.all_staff_uniqnames) > 1

    @staticmethod
    def take_next_dri() -> str:
        with QuestionAssigner.dri_lock:
            current_day = get_current_day()

            dri = QuestionAssigner.day_to_responsible_staff_uniqnames[current_day][0]
            QuestionAssigner.day_to_responsible_staff_uniqnames[current_day] = (
                QuestionAssigner.day_to_responsible_staff_uniqnames[current_day][1:] + [dri]
            )

            return dri

    @staticmethod
    def schedule_post(thread: StreamThread | ThreadSummary, from_backfill: bool) -> None:
        """Post a thread to slack once it is post_delay_minutes old."""
        remaining = 0.0

        try:
            due_at = datetime.fromisoformat(thread.created_at) + timedelta(
                minutes=QuestionAssigner.post_delay_minutes
            )

            remaining = (due_at - datetime.now(TIMEZONE)).total_seconds()
        except ValueError:
            LOG.warning(
                f"unreadable created_at {thread.created_at} on thread {thread.id}, posting now"
            )

        if remaining <= 0:
            QuestionAssigner.post_if_new(thread, from_backfill)
            return

        LOG.info(f"posting thread {thread.number} in {format_duration(remaining)}")

        timer = threading.Timer(
            remaining, QuestionAssigner.post_from_timer, args=(thread, from_backfill)
        )
        timer.daemon = True
        timer.start()

    @staticmethod
    def post_from_timer(thread: StreamThread | ThreadSummary, from_backfill: bool) -> None:
        try:
            QuestionAssigner.post_if_new(thread, from_backfill)
        except Exception:
            LOG.exception(f"could not post thread {thread.number}")

    @staticmethod
    def post_if_new(thread: StreamThread | ThreadSummary, from_backfill: bool) -> None:
        """Post a thread unless it is already posted or was deleted.

        The thread is fetched again because its answer status could have changed.
        If there's an accepted answer, it will post without a ping.
        If the question is getting backfilled from restart, it won't post anything.
        """
        with QuestionAssigner.post_lock:
            if store.get_question(thread.id) is not None:
                return

            detail = ed_client.get_thread(thread.id)

            if detail.is_err():
                error = detail.unwrap_err()

                if isinstance(error, HttpError) and error.status == 410:
                    LOG.info(f"skipping thread {thread.number}, it was deleted")
                    return

                LOG.warning(f"could not load thread {thread.id}, posting it as is: {error}")

                post_question(thread, QuestionAssigner.take_next_dri(), QuestionAssigner.can_request_extra_help())

                return

            current = detail.unwrap().thread

            if current.accepted_id is None:
                post_question(current, QuestionAssigner.take_next_dri(), QuestionAssigner.can_request_extra_help())
            elif from_backfill:
                LOG.info(f"skipping missed thread {thread.number}, it was already answered")
                return
            else:
                message_ts = post_question(current, None, QuestionAssigner.can_request_extra_help())

                store.set_accepted_id(current.id, current.accepted_id)

                store.set_status(current.id, store.ANSWERED)

                add_reaction(settings.SLACK_CHANNEL_ID, message_ts, "white_check_mark")

            first_answer = QuestionAssigner.find_first_staff_answer(current)

            if first_answer is not None:
                store.set_answer(current.id, *first_answer, current.accepted_id)


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
        """Pull in extra staff for a question.

        ``parent_ts`` is the original question message (where the reply goes);
        ``button_ts`` is whichever message the button was actually on, which is a
        threaded reply when help is requested after an answer was un-accepted.
        """
        current_day = get_current_day()

        # the assignee can't be their own extra help
        excluded = {dri}

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
        blocks = remove_button(button_blocks, ASK_FOR_HELP_ACTION_ID)
        blocks.append(build_context_block(context_text))

        app.client.chat_update(
            channel=channel_id,
            ts=button_ts,
            text=button_text,
            blocks=blocks,
        )


    @staticmethod
    def escalate_to_faculty(
        thread_id: int,
        channel_id: str,
        parent_ts: str,
        button_ts: str,
        button_blocks: list[dict],
        button_text: str,
        user_id: str,
    ) -> None:
        """Ping every faculty member on a question.

        ``parent_ts`` and ``button_ts`` work the same as in request_extra_help.
        ``user_id`` is the slack user id of whoever pressed the button.
        """
        if not settings.FACULTY_UNIQNAMES:
            LOG.warning("no faculty to escalate to")
            return

        mentions = ", ".join(mention_user_by_uniqname(faculty) for faculty in settings.FACULTY_UNIQNAMES)

        app.client.chat_postMessage(
            channel=channel_id,
            thread_ts=parent_ts,
            text=f"Escalating this question to faculty!\nFaculty: {mentions}",
            unfurl_links=False,
        )

        store.set_escalated_by(thread_id, user_id)

        blocks = remove_button(button_blocks, ESCALATE_TO_FACULTY_ACTION_ID)
        blocks.append(build_escalated_context_block(user_id))

        app.client.chat_update(
            channel=channel_id,
            ts=button_ts,
            text=button_text,
            blocks=blocks,
        )


    @staticmethod
    def take_random_helpers(count: int, exclude: set[str]) -> list[str]:
        """Take ``count`` staff off the helper deck, skipping anybody in ``exclude``.

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
    def list_all_course_threads() -> list[ThreadSummary]:
        threads: list[ThreadSummary] = []

        offset = 0

        while True:
            result = ed_client.list_course_threads(
                settings.COURSE_ID, limit=THREAD_PAGE_SIZE, offset=offset, sort="new"
            )

            if result.is_err():
                LOG.warning(
                    f"could not list threads at offset {offset}: {result.unwrap_err()}"
                )
                return threads

            page = result.unwrap().threads

            if not page:
                return threads

            threads += page

            offset += len(page)

    @staticmethod
    def backfill_missed_questions() -> None:
        """Post questions that came in while the bot was not listening.

        Fetch all posts and create Slack threads for questions we missed.
        """
        last_number = store.get_max_number()

        if last_number is None:
            LOG.info("nothing stored yet, so there is nothing to catch up on")
            return

        # oldest first, so the slack messages and the dri rotation stay in order
        missed = sorted(
            (
                thread
                for thread in QuestionAssigner.list_all_course_threads()
                if thread.number > last_number
            ),
            key=lambda thread: thread.number,
        )

        LOG.info(f"catching up on {len(missed)} thread(s) newer than number {last_number}")

        for thread in missed:
            if thread.type == "announcement":
                if settings.DISCORD_WEBHOOK_URL is not None and not store.is_announcement_sent(thread.id):
                    detail = ed_client.get_thread(thread.id)

                    if detail.is_err():
                        LOG.warning(f"could not load announcement {thread.id}: {detail.unwrap_err()}")
                    else:
                        post_announcement(detail.unwrap().thread)

                continue

            if thread.user_id in QuestionAssigner.staff_uniqnames_by_id and QuestionAssigner.skip_staff_posts:
                continue

            QuestionAssigner.schedule_post(thread, from_backfill=True)

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

        candidates = [
            thread.id
            for thread in QuestionAssigner.list_all_course_threads()
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

            first_answer = QuestionAssigner.find_first_staff_answer(thread)

            # is_answered is also true when only students answered
            if first_answer is None:
                LOG.info(f"no staff answer on thread {thread_id} to take a time from")
                continue

            if store.set_answer(thread_id, *first_answer, thread.accepted_id):
                filled += 1

        LOG.info(f"backfilled answers for {filled} thread(s)")

    @staticmethod
    def find_first_staff_answer(thread: Thread) -> tuple[str, str] | None:
        """The uniqname and time of the earliest staff answer on the edstem thread."""
        staff_answers = []

        for answer in thread.answers:
            if answer.user_id not in QuestionAssigner.staff_uniqnames_by_id:
                continue

            try:
                created_at = datetime.fromisoformat(answer.created_at)
            except ValueError:
                LOG.warning(
                    f"skipping answer {answer.id} on thread {thread.id}: "
                    f"unreadable created_at {answer.created_at}"
                )
                continue

            staff_answers.append((created_at, answer.user_id))

        if not staff_answers:
            return None

        answered_at, user_id = min(staff_answers)

        return (
            QuestionAssigner.staff_uniqnames_by_id[user_id],
            answered_at.astimezone(TIMEZONE).isoformat(),
        )

    @staticmethod
    def run_auto_help() -> None:
        while True:
            try:
                QuestionAssigner.auto_request_help()
            except Exception as exception:
                LOG.warning(f"could not auto-ask for help: {exception}")

            time.sleep(settings.AUTO_HELP_INTERVAL_SECONDS)

    @staticmethod
    def auto_request_help() -> None:
        """Ask for extra help on questions that have gone too long without a staff answer."""
        now = datetime.now(TIMEZONE)

        for question in store.get_unanswered_questions():
            if not question["message_text"].startswith(NEW_QUESTION_PREFIX):
                continue

            if question["assigned_at"] is None:
                continue

            age = now - datetime.fromisoformat(question["assigned_at"])

            if not (
                timedelta(minutes=QuestionAssigner.auto_help_after_minutes)
                <= age
                <= timedelta(hours=QuestionAssigner.auto_help_max_age_hours)
            ):
                continue

            thread_id = question["thread_id"]

            detail = ed_client.get_thread(thread_id)

            if detail.is_err():
                LOG.warning(f"could not load thread {thread_id}: {detail.unwrap_err()}")
                continue

            thread = detail.unwrap().thread

            first_answer = QuestionAssigner.find_first_staff_answer(thread)

            if first_answer is not None:
                store.set_answer(thread_id, *first_answer, thread.accepted_id)
                continue

            LOG.info(f"auto-asking for help on thread {thread_id}")

            escalated = question["escalated_at"] is not None

            button_blocks = [
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": question["message_text"]},
                },
                *build_question_buttons_blocks(
                    thread_id, question["dri"], QuestionAssigner.can_request_extra_help(), faculty_button=not escalated
                ),
            ]

            if escalated:
                button_blocks.append(build_escalated_context_block(question["escalated_by"]))

            QuestionAssigner.request_extra_help(
                thread_id=thread_id,
                dri=question["dri"],
                channel_id=question["channel_id"],
                parent_ts=question["message_ts"],
                button_ts=question["message_ts"],
                button_blocks=button_blocks,
                button_text=question["message_text"],
                context_text=(
                    "Help automatically requested after "
                    f"{format_duration(QuestionAssigner.auto_help_after_minutes * 60)}"
                    " without a staff answer"
                ),
            )

    @staticmethod
    def backfill_converted_posts() -> None:
        """Mark questions that were turned into posts while the bot was not listening."""
        for thread in QuestionAssigner.list_all_course_threads():
            if thread.type != "post":
                continue

            question = store.get_question(thread.id)

            if question is None:
                continue

            QuestionAssigner.mark_converted_to_post(question)

    @staticmethod
    def handle_new_thread(response: StreamServerMessage) -> None:
        event: StreamThreadNew = decode(StreamThreadNew, response.data)

        event_thread = event.thread

        if event_thread.course_id != settings.COURSE_ID:
            LOG.warning(f"Received message for course id {event_thread.course_id}")
            return

        if event_thread.type == "announcement":
            post_announcement(event_thread)
            return

        if event_thread.user_id in QuestionAssigner.staff_uniqnames_by_id and QuestionAssigner.skip_staff_posts:
            LOG.info(f"skipping post id {event_thread.id} because it was posted by a staff/admin\n")
            return

        QuestionAssigner.schedule_post(event_thread, from_backfill=False)




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

        if thread.get("type") == "post":
            QuestionAssigner.mark_converted_to_post(question)

            # posts have no accepted answer, so there is nothing else to track
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

            # staff_answerer is empty when no staff answered, e.g. a student's answer was accepted
            dri = question["staff_answerer"] or question["dri"]

            # dri is empty when the thread was posted with an accepted answer already on it
            if not dri:
                dri = QuestionAssigner.take_next_dri()

                store.set_dri(thread_id, dri)

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
                    *build_question_buttons_blocks(
                        thread_id,
                        dri,
                        QuestionAssigner.can_request_extra_help(),
                        faculty_button=question["escalated_at"] is None,
                    ),
                ],
            )

        store.set_accepted_id(thread_id, accepted_id)


    @staticmethod
    def mark_converted_to_post(question: sqlite3.Row) -> None:
        """Mark the slack message of a question that was turned into a post."""
        # threads that started as posts are tracked too
        if not question["message_text"].startswith(NEW_QUESTION_PREFIX):
            return

        if question["status"] == store.CONVERTED_TO_POST:
            return

        LOG.info(f"thread id {question['thread_id']} was turned into a post")

        add_reaction(question["channel_id"], question["message_ts"], "memo")

        app.client.chat_postMessage(
            channel=question["channel_id"],
            thread_ts=question["message_ts"],
            text="This question was turned into a post.",
        )

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

        store.set_status(question["thread_id"], store.CONVERTED_TO_POST)

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

        store.set_status(thread_id, store.DELETED)

        add_reaction(question["channel_id"], question["message_ts"], "wastebasket")

        app.client.chat_postMessage(
            channel=question["channel_id"],
            thread_ts=question["message_ts"],
            text="This thread was deleted.",
        )

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
