"""
Purpose: Central application configuration loader for runtime settings and file-backed secrets.
Input/Output: Reads environment variables, optional secret files, and YAML config paths; returns typed settings.
Invariants: Tokens are never hardcoded, paths can be relative, and list-like env vars can be provided as CSV.
Debugging: Call `Settings().safe_debug_snapshot()` or `/debug/snapshot` to inspect the active configuration with redaction.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from gateway.models.domain import (
    DockerMonitorConfig,
    DockerMonitorFile,
    HomeAssistantAliasConfig,
    TroubleshootingConfig,
)


def _split_csv(value: Any) -> Any:
    """Convert simple comma-separated environment values into Python lists."""
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


def _resolve_path(value: Path | None) -> Path | None:
    """Resolve relative paths from the current working directory for predictable Docker behavior."""
    if value is None:
        return None
    return value if value.is_absolute() else Path.cwd() / value


def _read_secret(secret_value: str | None, secret_file: Path | None) -> str | None:
    """Load a token from a mounted secret file when the direct environment variable is absent."""
    if secret_value:
        return secret_value
    resolved = _resolve_path(secret_file)
    if resolved and resolved.exists():
        return resolved.read_text(encoding="utf-8").strip()
    return None


class Settings(BaseSettings):
    """
    Purpose: Hold every environment-driven runtime setting in one typed object.
    Input/Output: Reads from `.env`, environment variables, and optional secret files.
    Invariants: Missing optional integrations degrade gracefully; required security checks stay explicit.
    Debugging: `safe_debug_snapshot()` returns the effective runtime view with secrets masked.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        enable_decoding=False,
        populate_by_name=True,
    )

    app_name: str = "secondbrain-voice-gateway"
    environment: str = "development"
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"
    request_timeout_seconds: float = 8.0
    debug_endpoints_enabled: bool = False
    trust_x_forwarded_for: bool = False
    reverse_proxy_ip_allowlist: list[str] = Field(default_factory=list)

    alexa_application_ids: list[str] = Field(default_factory=list)
    alexa_verify_signature: bool = False
    alexa_signature_tolerance_seconds: int = 150
    alexa_cert_cache_ttl_seconds: int = 3600
    alexa_enable_reprompt: bool = True

    secondbrain_enabled: bool = Field(True, validation_alias="SECOND_BRAIN_ENABLED")
    secondbrain_base_url: str = Field("http://secondbrain:8000", validation_alias="SECOND_BRAIN_BASE_URL")
    secondbrain_query_path: str = Field("/query", validation_alias="SECOND_BRAIN_QUERY_PATH")
    secondbrain_health_path: str = Field("/health", validation_alias="SECOND_BRAIN_HEALTH_PATH")
    secondbrain_query_field_name: str = Field("question", validation_alias="SECOND_BRAIN_QUERY_FIELD_NAME")
    secondbrain_bearer_token: str | None = Field(None, validation_alias="SECOND_BRAIN_BEARER_TOKEN")
    secondbrain_token_file: Path | None = Field(None, validation_alias="SECOND_BRAIN_TOKEN_FILE")

    home_assistant_enabled: bool = True
    home_assistant_base_url: str = "http://homeassistant:8123"
    home_assistant_token: str | None = None
    home_assistant_token_file: Path | None = None
    home_assistant_alias_config_path: Path = Path("configs/home_assistant_aliases.yml")

    docker_enabled: bool = True
    docker_base_url: str = "http://docker-proxy:2375"
    docker_monitors_config_path: Path = Path("configs/docker_services.yml")
    docker_include_log_hints: bool = True
    docker_logs_tail: int = 50

    troubleshooting_config_path: Path = Path("configs/troubleshooting_knowledge.yml")

    ai_enabled: bool = False
    ai_base_url: str | None = None
    ai_api_key: str | None = None
    ai_api_key_file: Path | None = None
    ai_model: str | None = None
    ai_timeout_seconds: float = 10.0

    @field_validator("alexa_application_ids", "reverse_proxy_ip_allowlist", mode="before")
    @classmethod
    def _parse_csv_fields(cls, value: Any) -> Any:
        return _split_csv(value)

    @field_validator(
        "secondbrain_token_file",
        "home_assistant_token_file",
        "ai_api_key_file",
        "home_assistant_alias_config_path",
        "docker_monitors_config_path",
        "troubleshooting_config_path",
        mode="before",
    )
    @classmethod
    def _parse_paths(cls, value: Any) -> Any:
        if value in (None, ""):
            return None
        return Path(value)

    @model_validator(mode="after")
    def _load_secret_files(self) -> "Settings":
        self.secondbrain_bearer_token = _read_secret(
            self.secondbrain_bearer_token,
            self.secondbrain_token_file,
        )
        self.home_assistant_token = _read_secret(
            self.home_assistant_token,
            self.home_assistant_token_file,
        )
        self.ai_api_key = _read_secret(self.ai_api_key, self.ai_api_key_file)
        return self

    def safe_debug_snapshot(self) -> dict[str, Any]:
        """Return a redacted operator-friendly configuration snapshot."""
        return {
            "app_name": self.app_name,
            "environment": self.environment,
            "host": self.host,
            "port": self.port,
            "log_level": self.log_level,
            "debug_endpoints_enabled": self.debug_endpoints_enabled,
            "trust_x_forwarded_for": self.trust_x_forwarded_for,
            "reverse_proxy_ip_allowlist": self.reverse_proxy_ip_allowlist,
            "alexa_application_ids": self.alexa_application_ids,
            "alexa_verify_signature": self.alexa_verify_signature,
            "secondbrain_enabled": self.secondbrain_enabled,
            "secondbrain_base_url": self.secondbrain_base_url,
            "secondbrain_query_path": self.secondbrain_query_path,
            "secondbrain_health_path": self.secondbrain_health_path,
            "secondbrain_bearer_token": self._mask(self.secondbrain_bearer_token),
            "home_assistant_enabled": self.home_assistant_enabled,
            "home_assistant_base_url": self.home_assistant_base_url,
            "home_assistant_token": self._mask(self.home_assistant_token),
            "home_assistant_alias_config_path": str(_resolve_path(self.home_assistant_alias_config_path)),
            "docker_enabled": self.docker_enabled,
            "docker_base_url": self.docker_base_url,
            "docker_monitors_config_path": str(_resolve_path(self.docker_monitors_config_path)),
            "docker_include_log_hints": self.docker_include_log_hints,
            "docker_logs_tail": self.docker_logs_tail,
            "troubleshooting_config_path": str(_resolve_path(self.troubleshooting_config_path)),
            "ai_enabled": self.ai_enabled,
            "ai_base_url": self.ai_base_url,
            "ai_model": self.ai_model,
            "ai_api_key": self._mask(self.ai_api_key),
        }

    @staticmethod
    def _mask(value: str | None) -> str | None:
        if not value:
            return None
        if len(value) <= 4:
            return "****"
        return f"{value[:2]}***{value[-2:]}"


def load_yaml_file(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    Purpose: Read a YAML file from disk with an operator-friendly default.
    Input/Output: Accepts a path and returns a parsed dictionary.
    Invariants: Missing files return the provided default instead of crashing the entire application.
    Debugging: Log the resolved path when configuration seems ignored.
    """

    resolved = _resolve_path(path)
    if resolved is None or not resolved.exists():
        return default or {}
    loaded = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    return loaded or {}


def load_home_assistant_alias_config(settings: Settings) -> HomeAssistantAliasConfig:
    """Load the Home Assistant entity and action allowlist from YAML."""
    data = load_yaml_file(settings.home_assistant_alias_config_path, default={})
    return HomeAssistantAliasConfig.model_validate(data)


def load_docker_monitor_config(settings: Settings) -> DockerMonitorFile:
    """Load monitored Docker containers and operator hints from YAML."""
    data = load_yaml_file(settings.docker_monitors_config_path, default={})
    return DockerMonitorFile.model_validate(data)


def load_troubleshooting_config(settings: Settings) -> TroubleshootingConfig:
    """Load static troubleshooting playbooks and grounded explanation text from YAML."""
    data = load_yaml_file(settings.troubleshooting_config_path, default={})
    return TroubleshootingConfig.model_validate(data)
