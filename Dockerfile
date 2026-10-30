FROM debian@sha256:99c0b25c46d0214d5a2091092e7908907dcaecd6a962cde0ac700acee222b6f3

RUN arch="$(dpkg --print-architecture)" && sed -i \
    -e 's|http://deb.debian.org/debian-security|http://snapshot.debian.org/archive/debian-security/20261027T000000Z|g' \
    -e 's|http://deb.debian.org/debian|http://snapshot.debian.org/archive/debian/20261027T000000Z|g' \
    -e '/^Signed-By:/a Check-Valid-Until: no' /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y --no-install-recommends \
    pciutils procps smartmontools $(if [ "$arch" = amd64 ]; then echo intel-gpu-tools; fi) \
    && rm -rf /var/lib/apt/lists/*

ENV LANG=C.UTF-8

WORKDIR /app
COPY sender /app/sender
CMD ["/app/sender"]
