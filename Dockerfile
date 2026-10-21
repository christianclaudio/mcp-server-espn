# syntax=docker/dockerfile:1
# Multi-stage build for mcp-server-espn
# Produces a minimal runtime image (~150MB) with no dev tooling.

# FastMCP's October 2026 certificate authority incident notice says to update the
# container's OS trust store: older base images ship a ca-certificates that predates the fix.
# python:3.12-slim is Debian trixie, where Debian shipped 20250419+deb13u1 in trixie-security.
# Both stages upgrade the package and fail the build if the installed version is older than
# this floor, so a base image change cannot drop the fix. The builder does it before any pip
# runs, because pip trusts the OS store as well as its bundled certifi.
# https://github.com/PrefectHQ/fastmcp/pull/5945 (notice) and
# https://security-tracker.debian.org/tracker/DSA-6572-1 (Debian floor)
ARG CA_CERTIFICATES_FLOOR=20250419+deb13u1

# ─── Stage 1: Builder ─────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

ARG CA_CERTIFICATES_FLOOR
RUN apt-get update && \
    apt-get install -y --no-install-recommends --only-upgrade ca-certificates && \
    rm -rf /var/lib/apt/lists/* && \
    installed="$(dpkg-query -W -f='${Version}' ca-certificates)" && \
    { dpkg --compare-versions "$installed" ge "$CA_CERTIFICATES_FLOOR" || \
      { echo "ca-certificates $installed is older than the floor $CA_CERTIFICATES_FLOOR" >&2; exit 1; }; }

WORKDIR /app

# Install build deps in a virtualenv so we can copy it cleanly
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY docker/pip-bootstrap.txt /tmp/pip-bootstrap.txt
COPY pyproject.toml README.md LICENSE ./
COPY src/ src/

# The build context has no .git, so uv-dynamic-versioning cannot read the tag and
# falls back to 0.0.0. Release builds pass the tag version, for example
# --build-arg UV_DYNAMIC_VERSIONING_BYPASS=1.3.1 (an unset or empty value keeps 0.0.0).
ARG UV_DYNAMIC_VERSIONING_BYPASS
ENV UV_DYNAMIC_VERSIONING_BYPASS=${UV_DYNAMIC_VERSIONING_BYPASS}

# pip 26.2.2 updates the certificates pip bundles, per FastMCP's October 2026 certificate
# authority incident notice. This image builds with pip, not uv, so pip carries the floor.
# https://github.com/PrefectHQ/fastmcp/blob/main/SECURITY.md#certificate-authority-incident-october-2026
# The bootstrap installs pip from a hash-pinned wheel (docker/pip-bootstrap.txt, --require-hashes),
# so it does not depend on the TLS trust of the base image's older pip and certifi; the maintainer
# who raises the pip floor updates that pin and its sha256 (Dependabot does not track the file).
RUN pip install --no-cache-dir --require-hashes --only-binary=:all: -r /tmp/pip-bootstrap.txt && \
    pip install --no-cache-dir .

# ─── Stage 2: Runtime ─────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# OS trust store update and floor check, the same as the builder (see CA_CERTIFICATES_FLOOR).
ARG CA_CERTIFICATES_FLOOR
RUN apt-get update && \
    apt-get install -y --no-install-recommends --only-upgrade ca-certificates && \
    rm -rf /var/lib/apt/lists/* && \
    installed="$(dpkg-query -W -f='${Version}' ca-certificates)" && \
    { dpkg --compare-versions "$installed" ge "$CA_CERTIFICATES_FLOOR" || \
      { echo "ca-certificates $installed is older than the floor $CA_CERTIFICATES_FLOOR" >&2; exit 1; }; }

# Security: run as non-root
RUN useradd --create-home --shell /bin/bash mcp
USER mcp
WORKDIR /home/mcp

# Copy the pre-built virtualenv from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# MCP servers communicate over stdio — no port to expose
ENTRYPOINT ["espn-mcp"]
