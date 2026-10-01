# syntax=docker/dockerfile:1
FROM coturn/coturn:4.18.0
USER root
RUN apt-get update && apt-get upgrade -y --no-install-recommends && rm -rf /var/lib/apt/lists/*
USER nobody:nogroup
