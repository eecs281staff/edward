# Edward

The helpful Edstem bot

## Architecture Components

* `slack_app.py`: Slack client operations, e.g. posting message to channel
* `store.py`: Database operations
* `question_assigner.py`
* `edstem_client.py`: Initializes the Edstem client
* `main.py`: Opens websocket to Edstem client

When receiving a new message, Edward will send a Slack message notifying the
user assigned to that question. This works through parsing `staff.csv`, where
each column is associated with a day of the week, and rows contain uniqnames for
staff.

There is an `ask for help` button which appears on questions, where the user
assigned can press, which pings other individuals. The way this works is by pinging
at least `3` other users, first prioritizing users that are also responsible for
that day, then pinging other users (until the minimum 3 is reached) via random
permutations.

There is also an `escalate to faculty` button, which pings the faculty for the course
when pressed. The faculty list is configured in the .local.env file

If a question goes 60 minutes without a staff answer, Edward asks for extra help on
it automatically, the same way the button does. Questions older than 24 hours are
skipped. Both times, and how often Edward checks, are set with `--auto-help-after`,
`--auto-help-max-age`, and `--auto-help-interval`.

There is also a configurable `--post-delay-minutes` flag which allows there to be a time gap between
receiving an Ed question and posting a message on Slack for the question. This argument is 0
by default.

Because Edward pings on every question, let the staff know to have their notifications
snoozed when they're sleeping/dont' want notifications.

Edward also tracks the status of questions. When it receives an event which adds an
"accepted" answer to a thread, it reacts to the message Edward sent with ✅. When a
question loses an "accepted" answer, Edward removes the reaction, and pings the
responsible staff.

It also handles posts that get deleted, and questions that were meant to be posts via other emoji
reactions.

If the relevant environment variable is set, Edward forwards every Edstem announcement to a Discord Webhook.
## Usage

Installation:

Use a local environment and install `requirements.txt`

Make sure you have set up `.local.env`. You can see an example with `ex.local.env`

Running the bot directly on your system:

`python3 src/main.py`

Alternatively, using Docker Compose:

`docker compose up --build`

NOTE: You must create `questions.db` first before starting the Docker container,
otherwise Docker will create a directory called `questions.db`, causing sqlite to fail.

## Testing

You can create a question using `src/create_test_question`

You can test other endpoints using a similar outline as the previously-mentioned file.

## Slack Setup

You'll need the following from Slack:  
SLACK_APP_TOKEN
SLACK_BOT_TOKEN
SLACK_CHANNEL_ID
Follow the instructions [here](https://docs.slack.dev/tools/bolt-python/getting-started/)
on how to get the first two. NOTE: You'll need admin permissions to add Edward to your workspace.
For the channel ID, right click on the channel you want
Edward to send messages in, Channel Details > View Channel Details, and you can copy
the Channel ID at the bottom. Make sure you add Edward to that channel!

