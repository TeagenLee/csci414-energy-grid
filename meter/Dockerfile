FROM python:3.12-slim
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      iproute2 iputils-ping traceroute iperf3 tcpdump netcat-openbsd curl openssl \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir requests==2.32.*
WORKDIR /app
COPY meter_sim.py ./
