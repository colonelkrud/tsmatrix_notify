# TSMatrixNotify

**TeamSpeak ↔ Matrix Notification Bridge**

TSMatrixNotify is a Python-based bridge that connects a TeamSpeak 3 server to a Matrix room.
It relays real-time TS3 events (joins, leaves, moves, kicks, bans) to Matrix and provides Matrix-side bot commands for monitoring and control.


---

## Features

### TeamSpeak Integration

* Connects via ServerQuery
* Subscribes to server + channel events
* Tracks connected users and session durations
* Announces:

  * Joins / leaves
  * Channel moves
  * Kicks / bans

### Matrix Bot

* Uses `simplematrixbotlib` + `matrix-nio`
* Sends TS3 event notifications to a room
* Supports commands:

  * `!ping` / `!p` – latency check
  * `!ts3health` / `!th` – TS3 connectivity + version
  * `!ts3online` / `!who` / `!list` – list online TS3 users
  * `!goodbot` / `!badbot` – feedback with stats
  * `!restart` / `!rs` – restart the bot
  * `!debug` / `!d` – run all diagnostics
  * `!help` / `!h` – show help

### Reliability & Recovery

* Automatic TS3 reconnect on socket failure
* Matrix homeserver preflight probe (`/_matrix/client/versions`)
* Exponential backoff with jitter
* Sync-response watchdog (detects stalled Matrix connections)
* Optional time-based watchdog (`--watchdog`)
* Graceful shutdown with session cleanup

### Cross-Platform

* Windows & Linux aware paths
* Uses OS-appropriate data/session directories
* No hardcoded file locations

---

## Requirements

* Python **3.10+**
* TeamSpeak 3 ServerQuery access
* A Matrix account + access token

### Python dependencies

Installed via:

```bash
pip install -r requirements.txt
```

Main libraries:

* `simplematrixbotlib`
* `matrix-nio`
* `aiohttp`
* `python-dotenv`
* `ts3API`

---

## Setup

### 1. Clone the repository

```bash
git clone https://github.com/yourname/tsmatrix_notify.git
cd tsmatrix_notify
```

### 2. Create a virtual environment (recommended)

```bash
python -m venv .venv
.venv\Scripts\activate   # Windows
# or
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

Create a `.env` file:

```env
# TeamSpeak
TS3_HOST=127.0.0.1
TS3_PORT=10011
TS3_USER=serveradmin
TS3_PASSWORD=your_password
TS3_VSERVER_ID=1

# Matrix
MATRIX_HOMESERVER=https://matrix.example.com
MATRIX_USER_ID=@bot:example.com
MATRIX_ACCESS_TOKEN=your_access_token
MATRIX_ROOM_ID=!roomid:example.com

# Optional
BOT_MESSAGES_FILE=bot_messages.json
WATCHDOG_TIMEOUT=1800
```

### 5. Start the bot

```bash
python tsmatrix_notify.py
```

---

## Command-Line Options

| Flag           | Description                   |
| -------------- | ----------------------------- |
| `--debug`      | Enable debug logging          |
| `--trace`      | Log full Matrix SyncResponses |
| `--no-startup` | Skip startup announcement     |
| `--watchdog`   | Enable time-based watchdog    |

Example:

```bash
python tsmatrix_notify.py --debug --watchdog
```

---

## Files Overview

| File                      | Purpose                              |
| ------------------------- | ------------------------------------ |
| `tsmatrix_notify.py`      | Main application                     |
| `requirements.txt`        | Python dependencies                  |
| `.env`                    | Local secrets/config (not committed) |
| `bot_messages.json`       | Praise/apology messages              |
| `bot_reviews_stats.json`  | Feedback counters                    |
| `tsmatrix_notify_run.bat` | Windows launcher                     |

---

## Security Notes

* **Never commit `.env`**
* Matrix access tokens grant full account access
* TS3 ServerQuery credentials are powerful
* Keep logs private if they contain internal hostnames

---

## Operational Behavior

* The bot runs inside its **own asyncio loop**
* All reconnects and restarts are automatic
* Matrix outages are handled silently with backoff
* TS3 socket drops are detected within ~12 seconds
* Presence reconciliation runs every 10 seconds

---

## Known Limitations

* TS3 event callbacks come from a background thread
* State is stored in memory (not persistent)
* Ban events may not always include `clid`
* Message delivery is best-effort (no retry queue)

---

## Roadmap

* Async queue for TS3 → Matrix events
* Persistent TS3 presence state
* Admin-only Matrix commands
* Multi-room support

---

## License
* GPLv3