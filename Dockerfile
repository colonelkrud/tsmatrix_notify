FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip wheel --wheel-dir /wheels -r requirements.txt

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    TSMATRIX_DATA_DIR=/data \
    MATRIX_SESSION_DIR=/data/session \
    HEALTHCHECK_HOST=0.0.0.0 \
    HEALTHCHECK_PORT=8080 \
    HEALTHCHECK_PATH_LIVE=/healthz/live \
    HEALTHCHECK_PATH_READY=/healthz/ready

WORKDIR /app

RUN groupadd --system app && useradd --system --create-home --gid app app

COPY --from=builder /wheels /wheels
COPY requirements.txt ./
RUN pip install --no-index --find-links=/wheels -r requirements.txt && rm -rf /wheels

COPY tsmatrix_notify ./tsmatrix_notify
COPY tsmatrix_notify.py ./
COPY bot_messages.json ./

RUN mkdir -p /data && chown -R app:app /app /data

USER app

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import os,urllib.request;port=os.getenv('HEALTHCHECK_PORT','8080');path=os.getenv('HEALTHCHECK_PATH_LIVE','/healthz/live');urllib.request.urlopen(f'http://127.0.0.1:{port}{path}',timeout=3)"

ENTRYPOINT ["python", "tsmatrix_notify.py"]
CMD []
