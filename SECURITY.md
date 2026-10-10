# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability, please do NOT open a public GitHub issue.

Please report vulnerabilities directly to the maintainer:
* **Email**: `christianclaudio@mail.com`

All reports will be acknowledged within 24 hours, and patches will be published with high priority.

## Release provenance

Each `v*` release attests the PyPI wheel and sdist and the GHCR image (by digest) from a separate `attest` job, the only job holding `attestations: write`. Verify with:

```bash
gh attestation verify mcp_server_espn-<version>-py3-none-any.whl --repo christianclaudio/mcp-server-espn
gh attestation verify mcp_server_espn-<version>.tar.gz --repo christianclaudio/mcp-server-espn
gh attestation verify oci://ghcr.io/christianclaudio/mcp-server-espn:<version> --repo christianclaudio/mcp-server-espn
```

Verifying the GHCR image needs registry access: run `docker login ghcr.io` (or have `gh` authenticated with `read:packages`) first, since `gh attestation verify oci://...` pulls the image manifest.
