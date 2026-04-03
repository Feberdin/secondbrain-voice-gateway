"""
Purpose: Query the existing SecondBrain REST API and normalize the answer for voice output.
Input/Output: Sends a POST request to `/query` and returns a grounded `StructuredAnswer`.
Invariants: Timeouts, auth failures, and empty responses become explicit operator-friendly messages.
Debugging: Check gateway logs, SecondBrain `/health`, and the configured bearer token when queries fail.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from gateway.config import Settings
from gateway.models.domain import EvidenceSnippet, HealthReport, ResultStatus, SourceType, StructuredAnswer

logger = logging.getLogger(__name__)


class SecondBrainAdapter:
    """
    Purpose: Encapsulate all communication with the upstream SecondBrain API.
    Input/Output: Accepts natural language questions and emits normalized answers.
    Invariants: The voice layer never needs to know raw SecondBrain response shapes.
    Debugging: Set `LOG_LEVEL=DEBUG` to inspect the normalized upstream payload keys.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def ask(self, question: str) -> StructuredAnswer:
        """Ask SecondBrain a question and summarize the response into voice-safe output."""
        if not self.settings.secondbrain_enabled:
            return StructuredAnswer(
                status=ResultStatus.ERROR,
                source=SourceType.SECOND_BRAIN,
                answer="SecondBrain integration is disabled.",
                next_step="Enable SecondBrain settings in the gateway configuration.",
            )

        url = f"{self.settings.secondbrain_base_url.rstrip('/')}{self.settings.secondbrain_query_path}"
        payload = {self.settings.secondbrain_query_field_name: question}

        try:
            async with httpx.AsyncClient(timeout=self.settings.request_timeout_seconds) as client:
                response = await client.post(url, json=payload, headers=self._headers())
                response.raise_for_status()
        except httpx.TimeoutException:
            return StructuredAnswer(
                status=ResultStatus.ERROR,
                source=SourceType.SECOND_BRAIN,
                answer="SecondBrain did not answer in time.",
                next_step="Check the SecondBrain app container, `/health`, and network reachability.",
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (401, 403):
                return StructuredAnswer(
                    status=ResultStatus.ERROR,
                    source=SourceType.SECOND_BRAIN,
                    answer="SecondBrain rejected the request.",
                    next_step="Check the bearer token configured for the voice gateway.",
                    raw={"status_code": exc.response.status_code},
                )
            return StructuredAnswer(
                status=ResultStatus.ERROR,
                source=SourceType.SECOND_BRAIN,
                answer="SecondBrain returned an API error.",
                next_step="Check the SecondBrain logs and confirm the `/query` endpoint is reachable.",
                raw={"status_code": exc.response.status_code},
            )
        except httpx.HTTPError as exc:
            logger.exception("SecondBrain request failed.")
            return StructuredAnswer(
                status=ResultStatus.ERROR,
                source=SourceType.SECOND_BRAIN,
                answer="I could not reach SecondBrain.",
                next_step="Check the base URL, DNS resolution, and container networking.",
                raw={"error": str(exc)},
            )

        raw = response.json()
        logger.debug("SecondBrain raw response keys: %s", list(raw.keys()) if isinstance(raw, dict) else type(raw))
        return self._normalize_response(raw)

    async def health_check(self) -> HealthReport:
        """Call the SecondBrain health endpoint for readiness and troubleshooting flows."""
        if not self.settings.secondbrain_enabled:
            return HealthReport(
                component="secondbrain",
                ok=True,
                detail="SecondBrain integration is disabled by configuration.",
                source=SourceType.SECOND_BRAIN,
            )

        url = f"{self.settings.secondbrain_base_url.rstrip('/')}{self.settings.secondbrain_health_path}"
        try:
            async with httpx.AsyncClient(timeout=self.settings.request_timeout_seconds) as client:
                response = await client.get(url, headers=self._headers())
                response.raise_for_status()
        except Exception as exc:  # noqa: BLE001 - we want one clear readiness status.
            return HealthReport(
                component="secondbrain",
                ok=False,
                detail=f"Health check failed: {exc}",
                source=SourceType.SECOND_BRAIN,
            )

        return HealthReport(
            component="secondbrain",
            ok=True,
            detail="SecondBrain health endpoint responded successfully.",
            source=SourceType.SECOND_BRAIN,
        )

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.settings.secondbrain_bearer_token:
            headers["Authorization"] = f"Bearer {self.settings.secondbrain_bearer_token}"
        return headers

    def _normalize_response(self, raw: Any) -> StructuredAnswer:
        """
        Why this exists: Existing SecondBrain deployments can evolve their response shape over time.
        What happens here: We probe several common keys and keep evidence snippets for operator debugging.
        Example input/output:
        - Input: {"answer": "Two contracts expire soon", "sources": [{"title": "Lease"}]}
        - Output: answer="Two contracts expire soon", evidence=[...]
        """

        if not isinstance(raw, dict):
            return StructuredAnswer(
                status=ResultStatus.UNCERTAIN,
                source=SourceType.SECOND_BRAIN,
                answer="SecondBrain returned an unexpected response format.",
                next_step="Check the upstream `/query` response shape and adjust the adapter if needed.",
                raw={"raw_type": str(type(raw))},
            )

        answer = self._first_text(raw, "answer", "summary", "result", "text", "message")
        evidence = self._collect_evidence(raw)

        if not answer:
            items = raw.get("items") or raw.get("results") or []
            if isinstance(items, list) and items:
                answer = self._summarize_items(items)

        if not answer:
            return StructuredAnswer(
                status=ResultStatus.UNCERTAIN,
                source=SourceType.SECOND_BRAIN,
                answer="SecondBrain returned data, but no short answer was available.",
                next_step="Check the upstream response body in the gateway debug logs.",
                evidence=evidence,
                raw=raw,
            )

        return StructuredAnswer(
            status=ResultStatus.OK,
            source=SourceType.SECOND_BRAIN,
            answer=answer.strip(),
            details=self._first_text(raw, "details", "debug", "context"),
            next_step="Ask a follow-up question if you want more detail.",
            evidence=evidence,
            raw=raw,
        )

    @staticmethod
    def _first_text(payload: dict[str, Any], *keys: str) -> str | None:
        for key in keys:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return None

    @staticmethod
    def _collect_evidence(payload: dict[str, Any]) -> list[EvidenceSnippet]:
        evidence: list[EvidenceSnippet] = []
        for key in ("sources", "documents", "facts"):
            raw_items = payload.get(key)
            if not isinstance(raw_items, list):
                continue
            for item in raw_items[:3]:
                if isinstance(item, dict):
                    evidence.append(
                        EvidenceSnippet(
                            title=str(item.get("title") or item.get("name") or key.title()),
                            snippet=str(item.get("snippet") or item.get("summary") or item.get("text") or "")[:240],
                            url=item.get("url"),
                        )
                    )
        return evidence

    @staticmethod
    def _summarize_items(items: list[Any]) -> str:
        parts: list[str] = []
        for item in items[:3]:
            if isinstance(item, dict):
                label = item.get("title") or item.get("name") or item.get("id") or "result"
                value = item.get("summary") or item.get("text") or item.get("status") or "available"
                parts.append(f"{label}: {value}")
            else:
                parts.append(str(item))
        return "Top results: " + "; ".join(parts)

