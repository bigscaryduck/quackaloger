# Quackaloger web UI — self-hosted media organizer (Unraid-friendly)
FROM python:3.12-slim

# tini for clean PID 1 signal handling; gosu to drop to PUID/PGID at runtime
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends tini gosu; \
    rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    XDG_CONFIG_HOME=/config \
    QUACK_WEB_HOST=0.0.0.0 \
    QUACK_WEB_PORT=8080 \
    QUACK_BROWSE_ROOTS=/data \
    QUACK_WATCH_POLLING=1 \
    PUID=99 \
    PGID=100 \
    UMASK=022

WORKDIR /app
COPY . /app
RUN pip install ".[web]"

RUN mkdir -p /config /data
VOLUME ["/config", "/data"]
EXPOSE 8080

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,os,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('QUACK_WEB_PORT','8080')+'/healthz').status==200 else 1)" || exit 1

ENTRYPOINT ["/usr/bin/tini", "--", "/entrypoint.sh"]
CMD ["quackaloger-web"]
