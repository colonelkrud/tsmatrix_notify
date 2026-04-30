# TSMatrixNotify

TeamSpeak 3 ➜ Matrix notification bridge. It subscribes to TeamSpeak events and posts join/leave/move/kick/ban updates into a Matrix room, plus Matrix bot commands for diagnostics.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q
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
| `MATRIX_ROOM_ID` | Destination room ID (`!room:example.com`) or room alias (`#ops:example.com`) |

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

> Security note: never commit credentials or `.env` files. Inject secrets at runtime via Docker/Helm/Kubernetes secrets or an external secret manager.

## Docker

Build:

```bash
docker build -t ghcr.io/colonelkrud/tsmatrix_notify:local .
```

Run:

```bash
docker run --rm --name tsmatrix-notify \
  --env-file .env \
  -p 8080:8080 \
  -v tsmatrix_notify_data:/data \
  ghcr.io/colonelkrud/tsmatrix_notify:local \
  --watchdog
```

GHCR image:

```bash
docker pull ghcr.io/colonelkrud/tsmatrix_notify:latest
docker pull ghcr.io/colonelkrud/tsmatrix_notify:sha-<shortsha>
```

## Helm

Chart path: `charts/tsmatrix-notify`.

Install with existing secret:

```bash
kubectl create secret generic tsmatrix-notify-secrets \
  --from-literal=MATRIX_ACCESS_TOKEN='***' \
  --from-literal=TS3_USER='serverquery' \
  --from-literal=TS3_PASSWORD='***'

helm upgrade --install tsmatrix-notify ./charts/tsmatrix-notify \
  --set config.matrixHomeserver=https://matrix.example.org \
  --set config.matrixUserId='@bot:example.org' \
  --set config.matrixRoomId='!room:example.org' \
  --set existingSecret.enabled=true \
  --set existingSecret.name=tsmatrix-notify-secrets
```

Install with chart-managed secret (still provided at deploy-time):

```bash
helm upgrade --install tsmatrix-notify ./charts/tsmatrix-notify \
  --set config.matrixHomeserver=https://matrix.example.org \
  --set config.matrixUserId='@bot:example.org' \
  --set config.matrixRoomId='!room:example.org' \
  --set secret.create=true \
  --set secret.matrixAccessToken='***' \
  --set secret.ts3User='serverquery' \
  --set secret.ts3Password='***'
```

Chart OCI package usage (release workflow output):

```bash
helm pull oci://ghcr.io/colonelkrud/charts/tsmatrix-notify --version <chart-version>
```


### Validation and secret handling

- `MATRIX_HOMESERVER` must be an `http://` or `https://` URL with a host.
- `MATRIX_USER_ID` must match `@user:server`.
- `MATRIX_ROOM_ID` must match `!room:server` or `#alias:server`.
- `TS3_PORT` and `HEALTHCHECK_PORT` must be between 1 and 65535; `TS3_VSERVER_ID` must be a positive integer.
- Health paths are normalized to begin with `/`.
- `MATRIX_ACCESS_TOKEN` is required and redacted in config logs.
## CI and release workflows

- CI (`.github/workflows/ci.yml`): tests, Docker build validation, Helm lint/template.
- Helm chart-testing (`.github/workflows/helm-ct.yml`): lints chart changes.
- Release (`.github/workflows/release.yml`): pushes Docker images and chart packages to GHCR on semver tags.

## Health endpoints

- `GET /healthz/live` → process liveness.
- `GET /healthz/ready` → readiness after Matrix startup callback completes.
- `GET /` → basic status payload.
