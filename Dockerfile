# syntax=docker/dockerfile:1
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# Unprivileged runtime user — nothing here ever runs as root. The dev compose builds it
# with the host user's ids, so files in mounted folders stay writable.
ARG APP_UID=10001
ARG APP_GID=10001
RUN groupadd --gid ${APP_GID} agent \
 && useradd --uid ${APP_UID} --gid ${APP_GID} --create-home --shell /usr/sbin/nologin agent

WORKDIR /app

# Dependencies come from pyproject.toml; the list is extracted with stdlib tomllib so
# this layer caches on pyproject.toml alone.
COPY pyproject.toml .
RUN python -c "import tomllib; open('/tmp/reqs.txt','w').write(chr(10).join(tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']))" \
 && pip install --no-cache-dir -r /tmp/reqs.txt \
 && rm /tmp/reqs.txt

COPY --chown=${APP_UID}:${APP_GID} common/ ./common/
COPY --chown=${APP_UID}:${APP_GID} main/ ./main/
COPY --chown=${APP_UID}:${APP_GID} agent_loop/ ./agent_loop/
COPY --chown=${APP_UID}:${APP_GID} compiler/ ./compiler/
COPY --chown=${APP_UID}:${APP_GID} runner/ ./runner/
COPY --chown=${APP_UID}:${APP_GID} executor/ ./executor/
COPY --chown=${APP_UID}:${APP_GID} scheduler/ ./scheduler/
COPY --chown=${APP_UID}:${APP_GID} internal_tools/ ./internal_tools/
COPY --chown=${APP_UID}:${APP_GID} launcher.py single_app.py ./

RUN mkdir -p /data /workspace && chown ${APP_UID}:${APP_GID} /data /workspace

USER ${APP_UID}:${APP_GID}

# One image, six services: compose picks the service with the command.
CMD ["python", "launcher.py", "main"]
