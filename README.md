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

Edward also tracks the status of questions. When it receives an event which adds an
"accepted" answer to a thread, it reacts to the message Edward sent with ✅. When a
question loses an "accepted" answer, Edward removes the reaction, and pings the
originally-responsible staff.

## Usage

Installation:

Use a local environment and install `requirements.txt`

Make sure you have set up `.local.env`. You can see an example with `ex.local.env`

Running the bot:

`python3 src/main.py`

You can create a question using `src/create_test_question`
