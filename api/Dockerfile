FROM python:3.12-slim
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      iproute2 iputils-ping traceroute iperf3 tcpdump netcat-openbsd \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir fastapi==0.115.* "uvicorn[standard]==0.32.*" pymongo==4.10.*
WORKDIR /app
COPY app.py import_uci.py ./
