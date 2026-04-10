FROM python:3.13-slim-trixie

ARG BESU_VERSION=26.2.0

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    REAL_INTERACT=1 \
    NODE_PATH=/opt/blockcap/node_modules

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    curl \
    gyp \
    jq \
    iproute2 \
    lsof \
    nodejs \
    npm \
    openjdk-21-jre-headless \
    python3-dev \
    python3-setuptools \
    procps \
    tar \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL "https://github.com/hyperledger/besu/releases/download/${BESU_VERSION}/besu-${BESU_VERSION}.tar.gz" -o /tmp/besu.tgz \
    && tar -xzf /tmp/besu.tgz -C /opt \
    && ln -sf "/opt/besu-${BESU_VERSION}/bin/besu" /usr/local/bin/besu \
    && rm -f /tmp/besu.tgz

WORKDIR /opt/blockcap

COPY requirements.txt /opt/blockcap/requirements.txt
COPY package.json /opt/blockcap/package.json
COPY package-lock.json /opt/blockcap/package-lock.json
COPY docker/blockcap-node/control_panel.py /opt/blockcap/control_panel.py

RUN pip install --no-cache-dir gyp-next \
    && pip install --no-cache-dir -r /opt/blockcap/requirements.txt \
    && npm install

COPY Node_root /opt/blockcap/Node_root
COPY runtime/templates /opt/blockcap/runtime/templates

WORKDIR /workspace

EXPOSE 8080

CMD ["python", "/opt/blockcap/control_panel.py"]
