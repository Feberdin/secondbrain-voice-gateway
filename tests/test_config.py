"""
Purpose: Verify environment parsing, secret-file loading, and safe debug snapshots.
Input/Output: These tests set temporary environment variables and instantiate `Settings`.
Invariants: CSV parsing and secret-file behavior must stay predictable for operators.
Debugging: Failures here usually mean an env var name changed or a validator became too strict.
"""

from __future__ import annotations

from pathlib import Path

from gateway.config import Settings


def test_settings_load_secret_file_and_csv(monkeypatch, tmp_path: Path) -> None:
    token_file = tmp_path / "secondbrain_token.txt"
    token_file.write_text("super-secret-token\n", encoding="utf-8")

    monkeypatch.setenv("SECOND_BRAIN_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("ALEXA_APPLICATION_IDS", "skill-a, skill-b")
    monkeypatch.setenv("ALEXA_ALLOWED_USER_IDS", "user-a, user-b")
    monkeypatch.setenv("REVERSE_PROXY_IP_ALLOWLIST", "127.0.0.1/32,10.0.0.0/24")

    settings = Settings(_env_file=None)

    assert settings.secondbrain_bearer_token == "super-secret-token"
    assert settings.alexa_application_ids == ["skill-a", "skill-b"]
    assert settings.alexa_allowed_user_ids == ["user-a", "user-b"]
    assert settings.reverse_proxy_ip_allowlist == ["127.0.0.1/32", "10.0.0.0/24"]


def test_safe_debug_snapshot_masks_tokens() -> None:
    settings = Settings(
        _env_file=None,
        secondbrain_bearer_token="abcdef123456",
        home_assistant_token="xyz98765",
        ai_api_key="tokenvalue",
    )

    snapshot = settings.safe_debug_snapshot()

    assert snapshot["secondbrain_bearer_token"] == "ab***56"
    assert snapshot["home_assistant_token"] == "xy***65"
    assert snapshot["ai_api_key"] == "to***ue"
