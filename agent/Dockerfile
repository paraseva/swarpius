# ---------- build stage: compile any C extensions ---------------------------
FROM python:3.13.13-slim-bookworm AS build

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements-server.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements-server.txt

# ---------- runtime stage: slim image with only what's needed ----------------
FROM python:3.13.13-slim-bookworm

# Run as a non-root user. UID 1000 matches typical desktop Linux/macOS
# accounts so host bind-mounts under ./agent/data/ remain writeable
# by the host user. Non-1000 hosts can override with
# `docker build --build-arg SWARPIUS_UID=$(id -u)`.
ARG SWARPIUS_UID=1000
RUN useradd --create-home --uid ${SWARPIUS_UID} swarpius

WORKDIR /app

COPY --from=build /install /usr/local
COPY --chown=swarpius:swarpius . .

USER swarpius

EXPOSE 8080

CMD ["python3", "agent.py", "--ws"]
