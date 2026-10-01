# Python is pinned by index digest; refresh it under the 24-hour age rule in AGENTS.md.
FROM docker.io/library/python:3.14-slim-trixie@sha256:51dafde81dbdb6ebde285137a295cf18a47ca95234fe388a343719cb97305b3d

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIHOLE_MANAGER_DATA=/data

WORKDIR /opt/pihole-manager

COPY requirements.lock ./
RUN pip install --no-cache-dir --root-user-action=ignore --require-hashes --only-binary=:all: -r requirements.lock

RUN groupadd --gid 10001 pihole-manager \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent \
       --shell /usr/sbin/nologin pihole-manager \
    && install -d -o 10001 -g 10001 -m 0700 /data

# The frontend is the committed build; the image never runs Node.
COPY app ./app

USER 10001:10001
VOLUME /data
EXPOSE 8420
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8420"]
