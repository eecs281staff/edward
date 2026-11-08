# Edward

The helpful Edstem bot

## Features

* Each new question is posted to Slack, and the staff member assigned to it is
  pinged.
* Each question's Slack message has an `ask for help` button. When it is
  pressed, at least 3 other staff are pinged, prioritizing others responsible
  for that day, then filling the rest via random permutations.
* Questions that go too long without a staff answer have other staff pinged for
  help automatically.
* Each question's Slack message has an `escalate to faculty` button. When it is
  pressed, the course faculty are pinged.
* Thread status is shown with emoji reactions on Slack messages:
  * ✅ when a question has an "accepted" answer. If it loses its accepted answer,
    the reaction is removed and the responsible staff are pinged.
  * 📝 when a question is turned into a post.
  * 🗑️ when a thread is deleted.
* Every Edstem announcement can be forwarded to a Discord webhook.

Because staff are pinged on every question, let them know to snooze their
notifications when they're sleeping or don't want notifications.

## Configuration Options

### `.env`

| Option | Default | Description |
| --- | --- | --- |
| `EDSTEM_API_KEY` | required | Edstem API token, obtained from <https://edstem.org/us/settings/api-tokens> |
| `EDSTEM_EMAIL` | required | Email for the bot's Edstem account, used to get the stream's session token |
| `EDSTEM_PASSWORD` | required | Password for the bot's Edstem account |
| `COURSE_ID` | required | Edstem course ID |
| `SLACK_APP_TOKEN` | required | Slack app token, see [Slack Setup](#slack-setup) |
| `SLACK_BOT_TOKEN` | required | Slack bot token, see [Slack Setup](#slack-setup) |
| `SLACK_CHANNEL_ID` | required | Slack channel Edward posts questions in |
| `SLACK_CANARY_CHANNEL_ID` | required | Slack channel the canary posts its heartbeat in |
| `FACULTY_UNIQNAMES` | empty | Comma-separated faculty uniqnames. The `escalate to faculty` button is hidden when empty |
| `DISCORD_WEBHOOK_URL` | empty | Discord webhook to forward announcements to. Forwarding is disabled when empty |
| `AUTO_HELP_AFTER_MINUTES` | `60` | Automatically ask for help on questions with no staff answer after this many minutes |
| `AUTO_HELP_MAX_AGE_HOURS` | `24` | Never automatically ask for help on questions older than this many hours |
| `AUTO_HELP_INTERVAL_SECONDS` | `60` | How often to check for questions that need help, in seconds |
| `POST_DELAY_MINUTES` | `0` | Wait this many minutes after a question is created before posting it to Slack |
| `INCLUDE_STAFF_POSTS` | `false` | Set to `true` to assign posts and questions created by staff, which are skipped by default |

### `staff.csv`

The staff schedule used for assignments. Each column is a day of the week and rows contain staff uniqnames.

### Slack Setup

You'll need a slack app token, bot token, and channel id.
Follow the instructions [here](https://docs.slack.dev/tools/bolt-python/getting-started/)
on how to get the first two.

> [!IMPORTANT]
> You'll need admin permissions to add Edward to your workspace.

For the channel ID, right click on the channel you want
Edward to send messages in, Channel Details > View Channel Details, and you can copy
the Channel ID at the bottom.

> [!IMPORTANT]
> Make sure you add Edward to that channel!

## Usage

### Installation

Use a local environment and install `requirements.txt`

Make sure you have the proper configurations in `.env` according to [Configuration Options](#configuration-options). You can copy from `.env.example` as a start.

### Execution

There are two options:

* Directly on your system:

```bash
python3 src/main.py
```

* Using Docker Compose:

```bash
touch questions.db
docker compose up --build
```

> [!WARNING]
> You must create `questions.db` first before starting the Docker container,
> otherwise Docker will create a directory called `questions.db`, causing sqlite to fail.

## Development

### Source Files

* `canary.py`: Health checks that post a heartbeat to the canary Slack channel
* `common.py`: Shared logger, timezone, and date helpers
* `create_test_question`: Script that creates a test question on Edstem
* `csvparser.py`: Parses `staff.csv` into a staff schedule by day
* `discord_webhook.py`: Forwards Edstem announcements to Discord
* `edstem/`: Edstem API and websocket stream client
  * `__init__.py`: Re-exports the package's public names
  * `client.py`: Edstem REST API client and login token request
  * `models.py`: Edstem API data types and JSON decoding
  * `stream.py`: Edstem websocket stream client and message types
* `edstem_client.py`: Initializes the Edstem client
* `env.py`: Reads variables from `.env`
* `get_thread_id`: Script that fetches an Edstem thread
* `main.py`: Opens websocket to Edstem client
* `question_assigner.py`: Assigns questions to staff and handles thread events
* `slack_app.py`: Slack client operations, e.g. posting message to channel
* `store.py`: Database operations
* `version.py`: Edward's version number

### Testing

You can create a question using `src/create_test_question`

You can test other endpoints using a similar outline as the previously-mentioned file.
