"""
Purpose: Verify Docker monitor matching and normalization for monitored containers.
Input/Output: These tests stub Docker API payloads and inspect the adapter result.
Invariants: Container name matching should be operator-friendly and robust to casing differences.
Debugging: Failures here usually mean a monitored container name no longer matches the real Docker names.
"""

from __future__ import annotations

from gateway.adapters.docker import DockerAdapter
from gateway.config import Settings
from gateway.models.domain import DockerMonitorConfig, DockerMonitorFile, ResultStatus


def _adapter() -> DockerAdapter:
    return DockerAdapter(
        Settings(_env_file=None, docker_enabled=True, docker_base_url="http://docker-proxy:2375"),
        DockerMonitorFile(
            containers=[
                DockerMonitorConfig(
                    key="jellyfin",
                    container_name="jellyfin",
                    friendly_name="Jellyfin",
                    aliases=["jellyfin"],
                    first_checks=["the container health status"],
                )
            ]
        ),
    )


async def test_find_container_payload_matches_case_insensitive_name() -> None:
    adapter = _adapter()

    async def fake_list_containers() -> list[dict[str, object]]:
        return [{"Id": "container-1", "Names": ["/Jellyfin"]}]

    adapter._list_containers = fake_list_containers  # type: ignore[method-assign]

    monitor = adapter.monitors()[0]
    container = await adapter._find_container_payload(monitor)

    assert container is not None
    assert container["Id"] == "container-1"


async def test_answer_status_question_reports_running_container() -> None:
    adapter = _adapter()

    async def fake_list_containers() -> list[dict[str, object]]:
        return [{"Id": "container-1", "Names": ["/Jellyfin"]}]

    async def fake_inspect_container(container_id: str) -> dict[str, object]:
        assert container_id == "container-1"
        return {
            "Id": "container-1",
            "Name": "/Jellyfin",
            "RestartCount": 0,
            "State": {
                "Status": "running",
                "Health": {"Status": "healthy"},
            },
        }

    adapter._list_containers = fake_list_containers  # type: ignore[method-assign]
    adapter._inspect_container = fake_inspect_container  # type: ignore[method-assign]

    answer = await adapter.answer_status_question("is Jellyfin running", matched_key="jellyfin")

    assert answer.status == ResultStatus.OK
    assert answer.answer == "Jellyfin läuft und ist gesund."
