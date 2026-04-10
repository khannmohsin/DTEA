FROM python:3.13-slim-trixie

ARG BESU_VERSION=26.2.0

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    REAL_INTERACT=1 \
    NODE_PATH=/app/node_modules \
    REPO_ROOT=/app/Node_root

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    curl \
    gyp \
    iproute2 \
    jq \
    lsof \
    nodejs \
    npm \
    openjdk-21-jre-headless \
    procps \
    python3-dev \
    python3-setuptools \
    tar \
    wget \
    && rm -rf /var/lib/apt/lists/*

RUN wget -q "https://github.com/hyperledger/besu/releases/download/${BESU_VERSION}/besu-${BESU_VERSION}.tar.gz" -O /tmp/besu.tgz \
    && tar -xzf /tmp/besu.tgz -C /opt \
    && ln -sf "/opt/besu-${BESU_VERSION}/bin/besu" /usr/local/bin/besu \
    && rm -f /tmp/besu.tgz

WORKDIR /app

COPY requirements.txt /app/requirements.txt
COPY package.json /app/package.json
COPY package-lock.json /app/package-lock.json

RUN pip install --no-cache-dir gyp-next \
    && pip install --no-cache-dir -r /app/requirements.txt \
    && npm install

COPY Node_root /app/Node_root
COPY runtime /app/runtime
COPY scripts /app/scripts
COPY entrypoint.sh /entrypoint.sh

RUN chmod +x /entrypoint.sh

EXPOSE 5600 8545 30303

ENTRYPOINT ["/entrypoint.sh"]
