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

Copy the example and edit it:

```bash
cp .env.example .env
```

Then set all required values in `.env`.

### 5. Start the bot

```bash
python tsmatrix_notify.py
```

---

## Quick start (Windows PowerShell)

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
# edit .env in your editor, then:
python tsmatrix_notify.py
```

## Quick start (Linux/macOS)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env in your editor, then:
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

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

Tests use fakes and do not contact real TeamSpeak or Matrix servers.

---

## Files Overview

| File                      | Purpose                              |
| ------------------------- | ------------------------------------ |
| `tsmatrix_notify.py`      | Main application                     |
| `tsmatrix_notify/`        | Hexagonal architecture package       |
| `requirements.txt`        | Python dependencies                  |
| `requirements-dev.txt`    | Test dependencies (optional)         |
| `pyproject.toml`          | Tooling configuration                |
| `.env.example`            | Sample environment file              |
| `tsmatrix_config.py`      | Configuration loading + validation   |
| `message_catalog.py`      | Praise/apology catalog loader        |
| `stats_store.py`          | Review stats persistence             |
| `.env`                    | Local secrets/config (not committed) |
| `bot_messages.json`       | Praise/apology messages              |
| `bot_reviews_stats.json`  | Feedback counters                    |
| `tsmatrix_notify_run.bat` | Windows launcher                     |
| `tests/`                  | Basic unit tests                     |

---

## Architecture Overview (Ports & Adapters)

The project is organized using a hexagonal architecture:

* **Domain** (`tsmatrix_notify/domain/`): pure logic (state, message formatting, event handling).
* **Ports** (`tsmatrix_notify/ports/`): typed interfaces for TeamSpeak, Matrix, and persistence.
* **Adapters** (`tsmatrix_notify/adapters/`): concrete implementations (ts3API, simplematrixbotlib, filesystem).
* **Main** (`tsmatrix_notify/main.py`): wiring, event loop, and runtime coordination.

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
* Matrix outages are handled silently with backoff (configuration errors fail fast)
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

---

## Troubleshooting

### Windows: Git "unable to create temporary file"

If you see errors like `unable to create temporary file`, check the following:

* **Long paths**: enable long paths in Windows Group Policy or registry.
* **Antivirus/Defender**: exclude the repo folder to prevent locks on `.git\` temp files.
* **Permissions**: run your shell as the same user that owns the repo.
* **Disk issues**: ensure the drive has free space and isn't set to read-only.

### Matrix or TS3 connection loops

* Verify required `.env` values are present and valid (invalid Matrix homeserver URLs now stop the bot with a clear error).
* Use `--debug` to see retry/backoff logs.

---

## Commands

| Command | Description |
| --- | --- |
| `!ping`, `!p` | latency check |
| `!ts3health`, `!th` | TS3 connectivity + version |
| `!ts3online`, `!who`, `!list` | list online TS3 users |
| `!goodbot`, `!badbot` | feedback with stats |
| `!restart`, `!rs` | restart the bot |
| `!debug`, `!d` | run all diagnostics |
| `!help`, `!h`, `!man` | show help |
