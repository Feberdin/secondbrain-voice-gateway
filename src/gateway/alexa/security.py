"""
Purpose: Validate Alexa application IDs, timestamps, and optional request signatures for production deployments.
Input/Output: Accepts raw request bytes, headers, and the parsed Alexa envelope; raises `ValueError` on validation failures.
Invariants: Signature verification is optional for local development but explicit and strict in production.
Debugging: When requests fail, inspect the cert URL, signature header, timestamp skew, and configured application IDs.
"""

from __future__ import annotations

import base64
import json
import logging
import posixpath
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import monotonic
from urllib.parse import urlparse

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.x509.oid import ExtensionOID

from gateway.alexa.models import AlexaRequestEnvelope
from gateway.config import Settings

logger = logging.getLogger(__name__)


@dataclass
class CachedCertificate:
    """Keep certificate bytes in memory for a short time to avoid a network fetch on every request."""

    pem_bytes: bytes
    expires_at: float


class AlexaRequestVerifier:
    """
    Purpose: Centralize Alexa trust checks so the route handler stays small and auditable.
    Input/Output: `verify()` accepts raw bytes, headers, and the parsed envelope.
    Invariants: Requests are rejected if the skill ID mismatches or the timestamp is stale.
    Debugging: Enable DEBUG logs to see which verification step failed first.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._cert_cache: dict[str, CachedCertificate] = {}

    async def verify(
        self,
        body_bytes: bytes,
        headers: dict[str, str],
        envelope: AlexaRequestEnvelope,
    ) -> None:
        """Run the configured Alexa validation steps."""
        logger.debug(
            "Starting Alexa verification for application_id=%s user_id_present=%s signature_check=%s",
            envelope.application_id,
            bool(envelope.user_id),
            self.settings.alexa_verify_signature,
        )
        self._verify_application_id(envelope)
        self._verify_user_id(envelope)
        self._verify_timestamp(envelope)
        if self.settings.alexa_verify_signature:
            await self._verify_signature(body_bytes, headers)

    def _verify_application_id(self, envelope: AlexaRequestEnvelope) -> None:
        if not self.settings.alexa_application_ids:
            return

        application_id = envelope.application_id
        if application_id not in self.settings.alexa_application_ids:
            raise ValueError("Alexa application ID is not allowed for this gateway.")

    def _verify_user_id(self, envelope: AlexaRequestEnvelope) -> None:
        """
        Why this exists: A public HTTPS endpoint is still reachable from the internet, so operators may want
        an additional gate that limits responses to one or a few Alexa accounts.
        What happens here: If a user allowlist is configured, the request must contain a matching Alexa user ID.
        Example input/output:
        - Input: userId not in `ALEXA_ALLOWED_USER_IDS`
        - Output: ValueError with a clear operator action
        """
        if not self.settings.alexa_allowed_user_ids:
            return

        user_id = envelope.user_id
        if not user_id:
            raise ValueError(
                "Alexa user ID is missing. Disable ALEXA_ALLOWED_USER_IDS or verify the incoming request envelope."
            )
        if user_id not in self.settings.alexa_allowed_user_ids:
            raise ValueError("Alexa user is not allowed for this gateway.")

    def _verify_timestamp(self, envelope: AlexaRequestEnvelope) -> None:
        now = datetime.now(UTC)
        delta = abs(now - envelope.request.timestamp.astimezone(UTC))
        tolerance = timedelta(seconds=self.settings.alexa_signature_tolerance_seconds)
        if delta > tolerance:
            raise ValueError("Alexa request timestamp is outside the allowed tolerance window.")

    async def _verify_signature(self, body_bytes: bytes, headers: dict[str, str]) -> None:
        signature_b64 = headers.get("signature-256")
        signature_hash = hashes.SHA256()
        signature_header_name = "Signature-256"
        if not signature_b64:
            signature_b64 = headers.get("signature")
            signature_hash = hashes.SHA1()
            signature_header_name = "Signature"
        cert_url = headers.get("signaturecertchainurl")
        if not signature_b64 or not cert_url:
            raise ValueError("Alexa signature headers are missing.")

        logger.debug("Fetching Alexa signing certificate from %s using %s", cert_url, signature_header_name)
        cert_pem = await self._fetch_certificate(cert_url)
        certificate = x509.load_pem_x509_certificate(cert_pem)
        self._validate_certificate_metadata(cert_url, certificate)

        signature = base64.b64decode(signature_b64)
        public_key = certificate.public_key()
        public_key.verify(signature, body_bytes, padding.PKCS1v15(), signature_hash)

        # Example I/O: {"request": {"timestamp": "..."}}
        # We parse once more here because Alexa signs the raw body, but timestamp validation needs the parsed value.
        json.loads(body_bytes.decode("utf-8"))

    async def _fetch_certificate(self, cert_url: str) -> bytes:
        cached = self._cert_cache.get(cert_url)
        if cached and cached.expires_at > monotonic():
            return cached.pem_bytes

        async with httpx.AsyncClient(timeout=self.settings.request_timeout_seconds) as client:
            response = await client.get(cert_url)
            response.raise_for_status()
            pem_bytes = response.content

        self._cert_cache[cert_url] = CachedCertificate(
            pem_bytes=pem_bytes,
            expires_at=monotonic() + self.settings.alexa_cert_cache_ttl_seconds,
        )
        return pem_bytes

    def _validate_certificate_metadata(self, cert_url: str, certificate: x509.Certificate) -> None:
        parsed = urlparse(cert_url)
        if parsed.scheme != "https":
            raise ValueError("Alexa certificate URL must use HTTPS.")
        if parsed.hostname != "s3.amazonaws.com":
            raise ValueError("Alexa certificate URL must point to s3.amazonaws.com.")
        if parsed.port not in (None, 443):
            raise ValueError("Alexa certificate URL must use port 443.")
        if parsed.query or parsed.fragment:
            raise ValueError("Alexa certificate URL must not include a query string or fragment.")

        normalized_path = posixpath.normpath(parsed.path)
        if not normalized_path.startswith("/echo.api/"):
            raise ValueError("Alexa certificate URL path must start with /echo.api/.")

        san_extension = certificate.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME)
        dns_names = san_extension.value.get_values_for_type(x509.DNSName)
        if "echo-api.amazon.com" not in dns_names:
            raise ValueError("Alexa certificate SAN does not contain echo-api.amazon.com.")

        now = datetime.now(UTC)
        if certificate.not_valid_before_utc > now or certificate.not_valid_after_utc < now:
            raise ValueError("Alexa certificate is not currently valid.")
