"""
Purpose: Optional IP allowlisting for deployments behind a trusted reverse proxy or internal network.
Input/Output: Middleware checks the client IP and rejects requests outside configured networks.
Invariants: No allowlist means no restriction; when enabled, requests fail closed.
Debugging: Confirm `trust_x_forwarded_for` and reverse proxy behavior before troubleshooting blocked requests.
"""

from __future__ import annotations

import ipaddress

from fastapi import HTTPException, Request

from gateway.config import Settings


def enforce_client_allowlist(request: Request, settings: Settings) -> None:
    """Reject requests from clients that are outside the configured allowlist."""
    if not settings.reverse_proxy_ip_allowlist:
        return

    candidate_ip = None
    if settings.trust_x_forwarded_for:
        forwarded_for = request.headers.get("x-forwarded-for", "")
        if forwarded_for:
            candidate_ip = forwarded_for.split(",")[0].strip()

    if not candidate_ip and request.client:
        candidate_ip = request.client.host

    if not candidate_ip:
        raise HTTPException(status_code=403, detail="Client IP address is unavailable.")

    client_ip = ipaddress.ip_address(candidate_ip)
    for network in settings.reverse_proxy_ip_allowlist:
        parsed_network = ipaddress.ip_network(network, strict=False)
        if client_ip in parsed_network:
            return

    raise HTTPException(status_code=403, detail="Client IP is not allowed for this gateway.")

