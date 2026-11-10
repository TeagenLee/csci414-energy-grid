FROM mongo:7.0
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      iproute2 iputils-ping \
    && rm -rf /var/lib/apt/lists/*
