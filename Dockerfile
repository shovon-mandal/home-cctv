# Home CCTV: one container with go2rtc, Motion, FFmpeg, rclone and the dashboard.
# Works on Linux, Windows and macOS (Docker Desktop), on x86-64, ARM64 and ARMv7.
ARG BASE=debian:trixie-slim
FROM ${BASE}

ARG GO2RTC_VERSION=v1.9.9
ARG TARGETARCH

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    TZ=UTC \
    CCTV_SUPERVISOR=1 \
    CCTV_FOLDERS=/etc/cctv/state/folders.json \
    CCTV_UPLOADED=/etc/cctv/state/uploaded.txt \
    HOME=/etc/cctv/state

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      motion ffmpeg rclone python3 ca-certificates curl tzdata util-linux procps iputils-ping iproute2 \
 && rm -rf /var/lib/apt/lists/*

# go2rtc (live streams). TARGETARCH is set by Docker: amd64, arm64 or arm.
RUN set -eu; \
    arch="${TARGETARCH:-}"; \
    if [ -z "$arch" ]; then case "$(uname -m)" in x86_64) arch=amd64;; aarch64) arch=arm64;; armv7*|armv6*) arch=arm;; esac; fi; \
    curl -fsSL -o /usr/local/bin/go2rtc \
      "https://github.com/AlexxIT/go2rtc/releases/download/${GO2RTC_VERSION}/go2rtc_linux_${arch}"; \
    chmod 755 /usr/local/bin/go2rtc; \
    go2rtc -version

COPY . /opt/cctv
RUN chmod 755 /opt/cctv/bin/cctv-* \
 && for f in /opt/cctv/bin/cctv-*; do ln -sf "$f" /usr/local/bin/; done \
 && date -u +%Y-%m-%d > /opt/cctv/VERSION \
 && mkdir -p /srv/cctv /run/cctv

# /etc/cctv            settings, accounts, Drive login, state
# /srv/cctv/sd-buffer  recordings
VOLUME ["/etc/cctv", "/srv/cctv/sd-buffer"]
EXPOSE 8085

HEALTHCHECK --interval=60s --timeout=10s --start-period=60s \
  CMD ["python3", "/opt/cctv/bin/cctv-healthcheck"]

CMD ["python3", "/opt/cctv/bin/cctv-supervisor"]
