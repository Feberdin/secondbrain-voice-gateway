"""
Purpose: Read Docker container status through a restricted socket proxy and summarize it for voice answers.
Input/Output: Calls the Docker Engine HTTP API via proxy and returns short health summaries.
Invariants: Only configured monitored containers are exposed to voice queries, and logs are summarized instead of read verbatim.
Debugging: Check the socket proxy permissions, monitored container names, and recent restart counts when answers look wrong.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from gateway.config import Settings
from gateway.models.domain import (
    DockerMonitorConfig,
    DockerMonitorFile,
    HealthReport,
    ResultStatus,
    SourceType,
    StructuredAnswer,
)

logger = logging.getLogger(__name__)


class DockerAdapter:
    """
    Purpose: Encapsulate safe Docker status access through a proxy instead of a raw privileged socket.
    Input/Output: Accepts a natural language question or a matched monitor key.
    Invariants: Voice users only learn about monitored containers, never the full Docker daemon surface.
    Debugging: Compare `/containers/json` and `/containers/<id>/json` through the proxy when container matching fails.
    """

    def __init__(self, settings: Settings, monitor_config: DockerMonitorFile) -> None:
        self.settings = settings
        self.monitor_config = monitor_config

    async def health_check(self) -> HealthReport:
        """Perform a lightweight Docker proxy readiness check."""
        if not self.settings.docker_enabled:
            return HealthReport(
                component="docker",
                ok=True,
                detail="Docker integration is disabled by configuration.",
                source=SourceType.DOCKER,
            )

        try:
            async with httpx.AsyncClient(timeout=self.settings.request_timeout_seconds) as client:
                response = await client.get(f"{self.settings.docker_base_url.rstrip('/')}/_ping")
                response.raise_for_status()
        except Exception as exc:  # noqa: BLE001 - readiness wants one clear state.
            return HealthReport(
                component="docker",
                ok=False,
                detail=f"Health check failed: {exc}",
                source=SourceType.DOCKER,
            )

        return HealthReport(
            component="docker",
            ok=True,
            detail="Docker proxy responded successfully.",
            source=SourceType.DOCKER,
        )

    async def answer_status_question(self, question: str, matched_key: str | None = None) -> StructuredAnswer:
        """Answer status questions for one monitored container or summarize issues across all monitors."""
        if not self.settings.docker_enabled:
            return StructuredAnswer(
                status=ResultStatus.ERROR,
                source=SourceType.DOCKER,
                answer="Docker integration is disabled.",
                next_step="Enable Docker settings in the gateway configuration.",
            )

        normalized = question.lower()
        if "unhealthy" in normalized or "which monitored containers" in normalized:
            return await self._summarize_unhealthy_containers()
        if "restart" in normalized or "restarts" in normalized:
            return await self._summarize_recent_restarts()

        monitor = self._find_monitor(question, matched_key)
        if not monitor:
            return StructuredAnswer(
                status=ResultStatus.UNCERTAIN,
                source=SourceType.DOCKER,
                answer="I am not sure which Docker service you mean.",
                next_step="Add that container alias to `configs/docker_services.yml`.",
            )

        try:
            container = await self._find_container_payload(monitor)
            if not container:
                return StructuredAnswer(
                    status=ResultStatus.ERROR,
                    source=SourceType.DOCKER,
                    answer=f"{monitor.friendly_name} is not visible through the Docker proxy.",
                    next_step="Check the monitored container name and proxy permissions.",
                    raw={"monitor": monitor.model_dump()},
                )

            inspect = await self._inspect_container(container["Id"])
        except httpx.HTTPError as exc:
            logger.exception("Docker proxy request failed.")
            return StructuredAnswer(
                status=ResultStatus.ERROR,
                source=SourceType.DOCKER,
                answer="I could not read Docker status.",
                next_step="Check the socket proxy URL, allowed endpoints, and Docker daemon reachability.",
                raw={"error": str(exc)},
            )

        return await self._normalize_single_container(question, monitor, inspect)

    def monitors(self) -> list[DockerMonitorConfig]:
        return self.monitor_config.containers

    def _find_monitor(self, question: str, matched_key: str | None = None) -> DockerMonitorConfig | None:
        if matched_key:
            for monitor in self.monitor_config.containers:
                if monitor.key == matched_key:
                    return monitor

        normalized = question.lower()
        best_match: tuple[int, DockerMonitorConfig] | None = None
        for monitor in self.monitor_config.containers:
            for phrase in {monitor.friendly_name.lower(), monitor.container_name.lower(), *[a.lower() for a in monitor.aliases]}:
                if phrase and phrase in normalized:
                    score = len(phrase)
                    if best_match is None or score > best_match[0]:
                        best_match = (score, monitor)
        return best_match[1] if best_match else None

    async def _summarize_unhealthy_containers(self) -> StructuredAnswer:
        unhealthy: list[str] = []
        for monitor in self.monitor_config.containers:
            container = await self._find_container_payload(monitor)
            if not container:
                unhealthy.append(f"{monitor.friendly_name} is missing")
                continue
            inspect = await self._inspect_container(container["Id"])
            health = self._health_state(inspect)
            status = self._status_value(inspect)
            if health == "unhealthy" or status not in {"running", "created"}:
                unhealthy.append(f"{monitor.friendly_name} is {health or status}")

        if not unhealthy:
            return StructuredAnswer(
                status=ResultStatus.OK,
                source=SourceType.DOCKER,
                answer="All monitored Docker services look healthy.",
            )

        return StructuredAnswer(
            status=ResultStatus.UNCERTAIN,
            source=SourceType.DOCKER,
            answer="These monitored services need attention: " + "; ".join(unhealthy[:3]),
            next_step="Ask about a specific container for a focused status and first checks.",
            raw={"unhealthy": unhealthy},
        )

    async def _summarize_recent_restarts(self) -> StructuredAnswer:
        restarted: list[str] = []
        for monitor in self.monitor_config.containers:
            container = await self._find_container_payload(monitor)
            if not container:
                continue
            inspect = await self._inspect_container(container["Id"])
            restart_count = int(inspect.get("RestartCount") or 0)
            if restart_count > 0:
                restarted.append(f"{monitor.friendly_name} restarted {restart_count} times")

        if not restarted:
            return StructuredAnswer(
                status=ResultStatus.OK,
                source=SourceType.DOCKER,
                answer="No monitored container reports recent restarts.",
            )

        return StructuredAnswer(
            status=ResultStatus.UNCERTAIN,
            source=SourceType.DOCKER,
            answer="Recent restart summary: " + "; ".join(restarted[:3]),
            next_step="Inspect the most restarted container and its health checks first.",
            raw={"restarts": restarted},
        )

    async def _normalize_single_container(
        self,
        question: str,
        monitor: DockerMonitorConfig,
        inspect: dict[str, Any],
    ) -> StructuredAnswer:
        """
        Why this exists: Docker inspection payloads are too verbose and technical for Alexa.
        What happens here: We reduce health, status, restart count, and log hints into one spoken summary.
        Example input/output:
        - Input: State.Status=running, Health.Status=healthy
        - Output: "Jellyfin is running and healthy."
        """

        status = self._status_value(inspect)
        health = self._health_state(inspect)
        restart_count = int(inspect.get("RestartCount") or 0)
        answer = f"{monitor.friendly_name} is {status}"
        if health:
            answer += f" and {health}"
        if restart_count:
            answer += f". It has restarted {restart_count} times"
        answer += "."

        next_step = None
        result_status = ResultStatus.OK
        if status != "running" or health == "unhealthy":
            result_status = ResultStatus.ERROR
            if monitor.first_checks:
                next_step = "Check " + ", then ".join(monitor.first_checks[:2]) + "."

        details = None
        if self.settings.docker_include_log_hints and (
            result_status != ResultStatus.OK or any(word in question.lower() for word in ("why", "logs", "failing", "error"))
        ):
            log_summary = await self._summarize_logs(inspect["Id"])
            if log_summary:
                details = log_summary

        return StructuredAnswer(
            status=result_status,
            source=SourceType.DOCKER,
            answer=answer,
            details=details,
            next_step=next_step,
            raw={"inspect": inspect, "monitor": monitor.model_dump()},
        )

    async def _list_containers(self) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=self.settings.request_timeout_seconds) as client:
            response = await client.get(f"{self.settings.docker_base_url.rstrip('/')}/containers/json?all=true")
            response.raise_for_status()
            return response.json()

    async def _inspect_container(self, container_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.settings.request_timeout_seconds) as client:
            response = await client.get(f"{self.settings.docker_base_url.rstrip('/')}/containers/{container_id}/json")
            response.raise_for_status()
            return response.json()

    async def _find_container_payload(self, monitor: DockerMonitorConfig) -> dict[str, Any] | None:
        containers = await self._list_containers()
        expected_name = monitor.container_name.strip().lower()
        for container in containers:
            names = [name.lstrip("/").strip().lower() for name in container.get("Names", [])]
            if expected_name in names:
                return container
        return None

    async def _summarize_logs(self, container_id: str) -> str | None:
        try:
            async with httpx.AsyncClient(timeout=self.settings.request_timeout_seconds) as client:
                response = await client.get(
                    f"{self.settings.docker_base_url.rstrip('/')}/containers/{container_id}/logs",
                    params={
                        "stdout": "true",
                        "stderr": "true",
                        "tail": str(self.settings.docker_logs_tail),
                    },
                )
                response.raise_for_status()
        except httpx.HTTPError:
            return None

        lines = self._decode_multiplexed_logs(response.content)
        interesting = [
            line.strip()
            for line in lines
            if any(keyword in line.lower() for keyword in ("error", "failed", "exception", "timeout", "refused"))
        ]
        if not interesting:
            return None
        return "Recent log hint: " + interesting[-1][:220]

    @staticmethod
    def _decode_multiplexed_logs(content: bytes) -> list[str]:
        if not content:
            return []

        lines: list[str] = []
        index = 0
        while index + 8 <= len(content):
            frame_length = int.from_bytes(content[index + 4 : index + 8], byteorder="big")
            payload_start = index + 8
            payload_end = payload_start + frame_length
            payload = content[payload_start:payload_end]
            if payload:
                lines.extend(payload.decode("utf-8", errors="ignore").splitlines())
            index = payload_end

        if not lines:
            lines = content.decode("utf-8", errors="ignore").splitlines()
        return lines

    @staticmethod
    def _status_value(inspect: dict[str, Any]) -> str:
        state = inspect.get("State", {})
        return str(state.get("Status") or "unknown")

    @staticmethod
    def _health_state(inspect: dict[str, Any]) -> str | None:
        state = inspect.get("State", {})
        health = state.get("Health", {})
        status = health.get("Status")
        return str(status) if status else None
