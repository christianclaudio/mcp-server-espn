# syntax=docker/dockerfile:1
# Multi-stage build for mcp-server-espn
# Produces a minimal runtime image (~150MB) with no dev tooling.

# ─── Stage 1: Builder ─────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /app

# Install build deps in a virtualenv so we can copy it cleanly
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

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
RUN pip install --no-cache-dir --upgrade "pip>=26.2.2" && \
    pip install --no-cache-dir .

# ─── Stage 2: Runtime ─────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# The same notice says to update the container's OS trust store: the base image's
# ca-certificates predates the fix. python:3.12-slim is Debian trixie, where Debian shipped
# 20250419+deb13u1 in trixie-security; the build fails if the installed package is older, so a
# base image change cannot drop the fix.
# https://github.com/PrefectHQ/fastmcp/pull/5945 (notice) and
# https://security-tracker.debian.org/tracker/DSA-6572-1 (Debian floor)
RUN apt-get update && \
    apt-get install -y --no-install-recommends --only-upgrade ca-certificates && \
    rm -rf /var/lib/apt/lists/* && \
    dpkg --compare-versions "$(dpkg-query -W -f='${Version}' ca-certificates)" ge 20250419+deb13u1

# Security: run as non-root
RUN useradd --create-home --shell /bin/bash mcp
USER mcp
WORKDIR /home/mcp

# Copy the pre-built virtualenv from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# MCP servers communicate over stdio — no port to expose
ENTRYPOINT ["espn-mcp"]

