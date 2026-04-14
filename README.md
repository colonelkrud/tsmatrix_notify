# TSMatrixNotify

TeamSpeak 3 ➜ Matrix notification bridge. It subscribes to TeamSpeak events and posts join/leave/move/kick/ban updates into a Matrix room, plus Matrix bot commands for diagnostics.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python tsmatrix_notify.py --debug --watchdog
```

Entrypoint command is `python tsmatrix_notify.py` and accepts `--debug`, `--trace`, `--no-startup`, and `--watchdog`.

## Environment variables

### Required

| Variable | Description |
|---|---|
| `TS3_USER` | TeamSpeak ServerQuery username |
| `TS3_PASSWORD` | TeamSpeak ServerQuery password |
| `MATRIX_HOMESERVER` | Matrix homeserver URL (`https://...`) |
| `MATRIX_USER_ID` | Matrix bot user ID (`@bot:example.com`) |
| `MATRIX_ACCESS_TOKEN` | Matrix access token |
| `MATRIX_ROOM_ID` | Destination room ID (`!room:example.com`) |

### Optional

| Variable | Default | Description |
|---|---:|---|
| `TS3_HOST` | `127.0.0.1` | TeamSpeak ServerQuery host |
| `TS3_PORT` | `10011` | TeamSpeak ServerQuery port |
| `TS3_VSERVER_ID` | `1` | Virtual server ID |
| `BOT_MESSAGES_FILE` | `bot_messages.json` | Praise/apology catalog file |
| `WATCHDOG_TIMEOUT` | `1800` | Timeout used by `--watchdog` |
| `MATRIX_SESSION_DIR` | OS-dependent | Matrix session directory |
| `MATRIX_SESSION_FILE` | `<session_dir>/matrix_session.json` | Matrix session file path |
| `TSMATRIX_DATA_DIR` | OS-dependent | Runtime data dir (`bot_reviews_stats.json`) |
| `HEALTHCHECK_HOST` | `0.0.0.0` | Health HTTP bind host |
| `HEALTHCHECK_PORT` | `8080` | Health HTTP bind port |
| `HEALTHCHECK_PATH_LIVE` | `/healthz/live` | Liveness path |
| `HEALTHCHECK_PATH_READY` | `/healthz/ready` | Readiness path |

> Do not commit secrets. Inject credentials using environment variables, Docker/Kubernetes secrets, or an external secret manager.

## Health endpoints

A lightweight HTTP server is started inside the process:

- `GET /healthz/live` → process liveness (200 when healthy, 503 when shutting down)
- `GET /healthz/ready` → readiness (200 only after Matrix startup callback completes)
- `GET /` → basic status payload

Use liveness for container restart checks and readiness for traffic gating (e.g., Kubernetes readinessProbe).

## Docker

### Build locally

```bash
docker build -t tsmatrix_notify:local .
```

### Run with `.env` and persistent storage

```bash
docker run -d --name tsmatrix_notify \
  --env-file .env \
  -v tsmatrix_notify_data:/data \
  -p 8080:8080 \
  ghcr.io/colonelkrud/tsmatrix_notify:latest \
  --watchdog
```

### Persistence guidance

Mount a writable volume at `/data` in containers. By default the image sets:

- `TSMATRIX_DATA_DIR=/data`
- `MATRIX_SESSION_DIR=/data/session`

This preserves Matrix session state and bot stats across restarts.

### GHCR image usage

```bash
docker pull ghcr.io/colonelkrud/tsmatrix_notify:latest
# or pin an immutable tag
docker pull ghcr.io/colonelkrud/tsmatrix_notify:sha-<shortsha>
```

## CI/CD container publishing

GitHub Actions workflow `.github/workflows/docker-image.yml`:

- Runs on pull requests (build validation for all PRs, plus publish for same-repo PR branches)
- Fork PRs do not publish because GHCR publish job only runs when PR head repo matches this repository
- Runs on pushes to `main` (build + push to GHCR)
- Uses Buildx with GitHub Actions cache
- Publishes tags:
  - `latest` (default branch)
  - `sha-<shortsha>`
  - `pr-<number>` for same-repo pull requests

## Tests

```bash
pip install -r requirements-dev.txt
pytest -q
```
