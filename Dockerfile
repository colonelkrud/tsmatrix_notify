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

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY tsmatrix_notify ./tsmatrix_notify
COPY tsmatrix_notify.py ./
COPY bot_messages.json ./

RUN mkdir -p /data && chown -R app:app /app /data

USER app

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import os,sys,urllib.request;port=os.getenv('HEALTHCHECK_PORT','8080');path=os.getenv('HEALTHCHECK_PATH_LIVE','/healthz/live');urllib.request.urlopen(f'http://127.0.0.1:{port}{path}',timeout=3);sys.exit(0)"

ENTRYPOINT ["python", "tsmatrix_notify.py"]
CMD []
