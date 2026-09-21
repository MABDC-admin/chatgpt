FROM python:3.12-bookworm

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

WORKDIR /srv

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl libreoffice-core libreoffice-writer-nogui \
 && rm -rf /var/lib/apt/lists/*

# Install Docker CLI (debian trixie docker.io no longer ships /usr/bin/docker)
ARG DOCKER_CLI_VERSION=27.5.1
RUN curl -fsSL "https://download.docker.com/linux/static/stable/x86_64/docker-${DOCKER_CLI_VERSION}.tgz" \
  | tar xz --strip-components=1 -C /usr/local/bin docker/docker

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini .

RUN mkdir -p /data/images /data/pptx

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
