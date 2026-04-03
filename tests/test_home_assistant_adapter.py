"""
Purpose: Verify Home Assistant state normalization for voice-friendly speech output.
Input/Output: Tests call normalization logic with representative Home Assistant payloads.
Invariants: Units and binary-state mappings should sound natural when Alexa reads them aloud.
Debugging: If wording sounds odd, adjust response templates or state maps in the alias YAML.
"""

from __future__ import annotations

import pytest

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


@pytest.mark.asyncio
async def test_home_assistant_adapter_returns_german_message_for_unknown_entity() -> None:
    adapter = HomeAssistantAdapter(Settings(_env_file=None), HomeAssistantAliasConfig(entities=[], actions=[]))

    result = await adapter.answer_state_question("wie voll sind meine ecoflow batterien")

    assert result.status == ResultStatus.UNCERTAIN
    assert result.answer == "Ich bin mir nicht sicher, welches Home-Assistant-Gerät oder welcher Sensor gemeint ist."
    assert "configs/home_assistant_aliases.yml" in (result.next_step or "")
