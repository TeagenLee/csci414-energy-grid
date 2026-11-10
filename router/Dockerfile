FROM ubuntu:24.04
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      iproute2 nftables tcpdump iputils-ping traceroute iperf3 netcat-openbsd \
    && rm -rf /var/lib/apt/lists/*
