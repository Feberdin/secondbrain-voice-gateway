"""
Purpose: Verify Home Assistant state normalization for voice-friendly speech output.
Input/Output: Tests call normalization logic with representative Home Assistant payloads.
Invariants: Units and binary-state mappings should sound natural when Alexa reads them aloud.
Debugging: If wording sounds odd, adjust response templates or state maps in the alias YAML.
"""

from __future__ import annotations

from gateway.adapters.home_assistant import HomeAssistantAdapter
from gateway.config import Settings
from gateway.models.domain import HomeAssistantAliasConfig, HomeAssistantStateAlias, ResultStatus


def test_home_assistant_adapter_formats_percent_state() -> None:
    alias = HomeAssistantStateAlias(
        key="ecoflow_battery_soc",
        friendly_name="EcoFlow battery",
        entity_id="sensor.ecoflow_battery_soc",
        aliases=["ecoflow battery"],
        response_template="{friendly_name} is at {value}.",
    )
    adapter = HomeAssistantAdapter(Settings(_env_file=None), HomeAssistantAliasConfig(entities=[alias], actions=[]))

    result = adapter._normalize_state(alias, {"state": "78", "attributes": {"unit_of_measurement": "%"}})

    assert result.status == ResultStatus.OK
    assert result.answer == "EcoFlow battery is at 78 percent."


def test_home_assistant_adapter_maps_binary_sensor() -> None:
    alias = HomeAssistantStateAlias(
        key="paperless_online",
        friendly_name="Paperless availability",
        entity_id="binary_sensor.paperless_online",
        aliases=["paperless status"],
        state_map={"on": "available", "off": "unavailable"},
        response_template="{friendly_name} is {value}.",
    )
    adapter = HomeAssistantAdapter(Settings(_env_file=None), HomeAssistantAliasConfig(entities=[alias], actions=[]))

    result = adapter._normalize_state(alias, {"state": "on", "attributes": {}})

    assert result.answer == "Paperless availability is available."

