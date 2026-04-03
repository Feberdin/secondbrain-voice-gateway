"""
Purpose: Serve grounded troubleshooting answers from static knowledge plus lightweight live checks.
Input/Output: Matches a troubleshooting question to a configured playbook and returns a normalized answer.
Invariants: The service states clearly whether a statement came from static guidance or a live system check.
Debugging: Extend `configs/troubleshooting_knowledge.yml` when a recurring issue needs a more specific answer.
"""

from __future__ import annotations

from typing import Iterable

from gateway.models.domain import (
    HealthReport,
    ResultStatus,
    SourceType,
    StructuredAnswer,
    TroubleshootingConfig,
    TroubleshootingEntry,
)


class TroubleshootingService:
    """
    Purpose: Keep operational guidance and live health probes together for support-style questions.
    Input/Output: Accepts a question or matched troubleshooting key and returns grounded guidance.
    Invariants: Static advice is clearly labeled, and live checks are never invented if they were not executed.
    Debugging: Inspect the `live_checks` section in the YAML file when expected probes do not run.
    """

    def __init__(
        self,
        config: TroubleshootingConfig,
        secondbrain_adapter: object | None = None,
        home_assistant_adapter: object | None = None,
        docker_adapter: object | None = None,
    ) -> None:
        self.config = config
        self.secondbrain_adapter = secondbrain_adapter
        self.home_assistant_adapter = home_assistant_adapter
        self.docker_adapter = docker_adapter

    async def answer(self, question: str, matched_key: str | None = None) -> StructuredAnswer:
        entry = self._find_entry(question, matched_key)
        if not entry:
            return StructuredAnswer(
                status=ResultStatus.UNCERTAIN,
                source=SourceType.TROUBLESHOOTING,
                answer="I do not have a dedicated troubleshooting note for that yet.",
                next_step="Add a grounded entry to `configs/troubleshooting_knowledge.yml` for recurring issues.",
            )

        health_reports = await self._run_live_checks(entry.live_checks)
        live_summary = self._summarize_live_checks(health_reports)
        details = (
            "Configured troubleshooting guidance. "
            + (" Live checks: " + live_summary if live_summary else " No live checks were configured.")
        )
        next_step = entry.steps[0] if entry.steps else None

        answer = entry.summary
        if live_summary:
            answer = f"{entry.summary} Live status: {live_summary}."

        return StructuredAnswer(
            status=ResultStatus.OK if all(report.ok for report in health_reports) else ResultStatus.UNCERTAIN,
            source=SourceType.TROUBLESHOOTING,
            answer=answer,
            details=details,
            next_step=next_step,
            raw={
                "entry": entry.model_dump(),
                "live_checks": [report.model_dump() for report in health_reports],
            },
        )

    def explain_system(self) -> StructuredAnswer:
        """Return a concise built-in explanation of SecondBrain and the gateway capabilities."""
        answer = (
            "SecondBrain is a self-hosted companion for Paperless. "
            "Paperless stays the archive and source of truth. "
            "This gateway can answer from SecondBrain, read live Home Assistant data, check Docker services, "
            "and run a few explicitly approved Home Assistant actions."
        )
        return StructuredAnswer(
            status=ResultStatus.OK,
            source=SourceType.LOCAL,
            answer=answer,
            details=self.config.about_secondbrain,
            next_step="Ask about contracts, battery status, a container, or a safe action like EV charging.",
        )

    def entries(self) -> list[TroubleshootingEntry]:
        return self.config.entries

    def _find_entry(self, question: str, matched_key: str | None = None) -> TroubleshootingEntry | None:
        if matched_key:
            for entry in self.config.entries:
                if entry.key == matched_key:
                    return entry

        normalized = question.lower()
        best: tuple[int, TroubleshootingEntry] | None = None
        for entry in self.config.entries:
            for pattern in entry.patterns:
                if pattern.lower() in normalized:
                    score = len(pattern)
                    if best is None or score > best[0]:
                        best = (score, entry)
        return best[1] if best else None

    async def _run_live_checks(self, checks: Iterable[str]) -> list[HealthReport]:
        reports: list[HealthReport] = []
        for check in checks:
            if check == "secondbrain_health" and self.secondbrain_adapter:
                reports.append(await self.secondbrain_adapter.health_check())
            elif check == "home_assistant_health" and self.home_assistant_adapter:
                reports.append(await self.home_assistant_adapter.health_check())
            elif check == "docker_health" and self.docker_adapter:
                reports.append(await self.docker_adapter.health_check())
        return reports

    @staticmethod
    def _summarize_live_checks(reports: list[HealthReport]) -> str | None:
        if not reports:
            return None
        return "; ".join(
            f"{report.component} is {'ok' if report.ok else 'failing'}"
            for report in reports
        )

