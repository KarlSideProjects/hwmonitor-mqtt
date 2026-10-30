# syntax=docker/dockerfile:1
FROM debian@sha256:99c0b25c46d0214d5a2091092e7908907dcaecd6a962cde0ac700acee222b6f3

RUN apt-get update && apt-get install -y --no-install-recommends \
    intel-gpu-tools pciutils procps smartmontools \
    && rm -rf /var/lib/apt/lists/*

ENV LANG=C.UTF-8

WORKDIR /app
COPY sender /app/sender
CMD ["/app/sender"]
