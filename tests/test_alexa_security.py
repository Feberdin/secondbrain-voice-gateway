"""
Purpose: Verify Alexa request signature handling for current and legacy header variants.
Input/Output: These tests call the verifier directly with mocked certificates and headers.
Invariants: Modern `Signature-256` requests must validate with SHA-256, while legacy `Signature` stays supported.
Debugging: If live Alexa requests fail verification, compare their headers with the cases covered here.
"""

from __future__ import annotations

import base64

from cryptography.hazmat.primitives import hashes
import pytest

from gateway.alexa.security import AlexaRequestVerifier
from gateway.config import Settings


class RecordingPublicKey:
    """Small test double that records which digest algorithm the verifier selects."""

    def __init__(self) -> None:
        self.algorithm_name: str | None = None

    def verify(self, signature, body_bytes, padding_scheme, algorithm) -> None:  # noqa: ANN001
        del signature, body_bytes, padding_scheme
        self.algorithm_name = algorithm.name


class RecordingCertificate:
    """Return the recording public key without needing a real X.509 certificate fixture."""

    def __init__(self, public_key: RecordingPublicKey) -> None:
        self._public_key = public_key

    def public_key(self) -> RecordingPublicKey:
        return self._public_key


async def _fake_fetch_certificate(cert_url: str) -> bytes:
    del cert_url
    return b"fake-cert"


@pytest.mark.asyncio
async def test_verify_signature_prefers_signature_256(monkeypatch) -> None:
    public_key = RecordingPublicKey()
    verifier = AlexaRequestVerifier(Settings(_env_file=None, alexa_verify_signature=True))

    monkeypatch.setattr(verifier, "_fetch_certificate", _fake_fetch_certificate)
    monkeypatch.setattr(verifier, "_validate_certificate_metadata", lambda cert_url, certificate: None)
    monkeypatch.setattr(
        "gateway.alexa.security.x509.load_pem_x509_certificate",
        lambda pem_bytes: RecordingCertificate(public_key),
    )

    await verifier._verify_signature(
        body_bytes=b'{"request":{"timestamp":"2026-04-03T16:45:40Z"}}',
        headers={
            "signature-256": base64.b64encode(b"sig256").decode("ascii"),
            "signaturecertchainurl": "https://s3.amazonaws.com/echo.api/echo-api-cert.pem",
        },
    )

    assert public_key.algorithm_name == hashes.SHA256().name


@pytest.mark.asyncio
async def test_verify_signature_keeps_legacy_signature_fallback(monkeypatch) -> None:
    public_key = RecordingPublicKey()
    verifier = AlexaRequestVerifier(Settings(_env_file=None, alexa_verify_signature=True))

    monkeypatch.setattr(verifier, "_fetch_certificate", _fake_fetch_certificate)
    monkeypatch.setattr(verifier, "_validate_certificate_metadata", lambda cert_url, certificate: None)
    monkeypatch.setattr(
        "gateway.alexa.security.x509.load_pem_x509_certificate",
        lambda pem_bytes: RecordingCertificate(public_key),
    )

    await verifier._verify_signature(
        body_bytes=b'{"request":{"timestamp":"2026-04-03T16:45:40Z"}}',
        headers={
            "signature": base64.b64encode(b"legacy-sig").decode("ascii"),
            "signaturecertchainurl": "https://s3.amazonaws.com/echo.api/echo-api-cert.pem",
        },
    )

    assert public_key.algorithm_name == hashes.SHA1().name
