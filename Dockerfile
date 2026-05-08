# syntax=docker/dockerfile:1
# Multi-stage build:
#   1. Builder stage builds the wheel.
#   2. Runtime stage installs the wheel and nothing else.
# Final image is python:3.13-slim + the sentinel-dns wheel + its deps.

FROM python:3.13-slim AS builder

WORKDIR /build

# Copy what hatchling needs to build the wheel — pyproject.toml, the
# package source, and the metadata files referenced from the project
# table (README.md is the readme, LICENSE is the license file).
COPY pyproject.toml README.md LICENSE ./
COPY sentinel_dns/ sentinel_dns/

RUN pip install --no-cache-dir build && \
    python -m build --wheel


FROM python:3.13-slim

LABEL org.opencontainers.image.source="https://github.com/MoazzamSameer/sentinel-dns"
LABEL org.opencontainers.image.description="AI-assisted DNS resolver — explains every block in plain English."
LABEL org.opencontainers.image.licenses="MIT"

# Run as a non-root user. Privileged ports (53) require either
# `--cap-add=NET_BIND_SERVICE` from the operator, port-mapping
# (`-p 53:5354/udp`), or `--user 0`. Documented in docs/docker.md.
RUN useradd --system --create-home --shell /bin/bash --uid 1000 sentinel

USER sentinel
WORKDIR /home/sentinel

COPY --from=builder /build/dist/*.whl /tmp/sentinel.whl
RUN pip install --no-cache-dir --user /tmp/sentinel.whl && \
    rm /tmp/sentinel.whl

ENV PATH=/home/sentinel/.local/bin:$PATH

# Default unprivileged port. Override with --listen-port 53 when the
# container has CAP_NET_BIND_SERVICE or runs as root, or use
# `-p 53:5354/udp` to map the host's 53 to the container's 5354.
EXPOSE 5354/udp

# DNS-style health check: query localhost:5354 for example.com.
# python is the one tool already on PATH; use dnspython (a runtime
# dep) to avoid bundling dig. Returns 0 if the resolver answers,
# nonzero otherwise.
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import socket,struct,sys; s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.settimeout(3); q=b'\\x12\\x34\\x01\\x00\\x00\\x01\\x00\\x00\\x00\\x00\\x00\\x00\\x07example\\x03com\\x00\\x00\\x01\\x00\\x01'; s.sendto(q,('127.0.0.1',5354)); s.recv(512); s.close()" || exit 1

ENTRYPOINT ["sentinel-dns"]
