"""
Purpose: Query the existing SecondBrain REST API and normalize the answer for voice output.
Input/Output: Sends a POST request to `/query` and returns a grounded `StructuredAnswer`.
Invariants: Timeouts, auth failures, and empty responses become explicit operator-friendly messages.
Debugging: Check gateway logs, SecondBrain `/health`, and the configured bearer token when queries fail.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from gateway.config import Settings
from gateway.models.domain import EvidenceSnippet, HealthReport, ResultStatus, SourceType, StructuredAnswer

logger = logging.getLogger(__name__)

RETRIEVAL_DEBUG_PATTERNS = (
    re.compile(r"found\s+\d+\s+structured\s+matches", re.IGNORECASE),
    re.compile(r"\d+\s+semantic\s+context\s+matches", re.IGNORECASE),
    re.compile(r"adaptive\s+retrieval\s+limit", re.IGNORECASE),
)


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
        configured_field = (self.settings.secondbrain_query_field_name or "question").strip() or "question"

        try:
            async with httpx.AsyncClient(timeout=self.settings.request_timeout_seconds) as client:
                response = await self._post_query_with_fallback(
                    client=client,
                    url=url,
                    question=question,
                    configured_field=configured_field,
                )
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
            if exc.response.status_code == 422:
                return StructuredAnswer(
                    status=ResultStatus.ERROR,
                    source=SourceType.SECOND_BRAIN,
                    answer="SecondBrain rejected the query payload.",
                    next_step=(
                        "Set SECOND_BRAIN_QUERY_FIELD_NAME to the upstream field name. "
                        "Common values are `query` and `question`."
                    ),
                    raw={
                        "status_code": exc.response.status_code,
                        "detail": self._safe_json(exc.response),
                    },
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

    async def _post_query_with_fallback(
        self,
        client: httpx.AsyncClient,
        url: str,
        question: str,
        configured_field: str,
    ) -> httpx.Response:
        """
        Why this exists: Real SecondBrain deployments have used both `question` and `query` as POST field names.
        What happens here: We try the configured field first and only retry on a 422 schema-style rejection.
        Example input/output:
        - Input: configured_field="question", upstream says body.query is required
        - Output: retry once with `query`
        """

        attempted_fields: list[str] = []
        response: httpx.Response | None = None
        for field_name in self._candidate_query_fields(configured_field):
            attempted_fields.append(field_name)
            response = await client.post(url, json={field_name: question}, headers=self._headers())
            if response.status_code != 422:
                return response

            hinted_field = self._extract_missing_body_field_name(response)
            if hinted_field and hinted_field not in attempted_fields:
                logger.info(
                    "SecondBrain returned 422 for field '%s'. Retrying once with hinted field '%s'.",
                    field_name,
                    hinted_field,
                )
                response = await client.post(url, json={hinted_field: question}, headers=self._headers())
                return response

        assert response is not None  # A response always exists after the first POST attempt.
        return response

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

    @staticmethod
    def _candidate_query_fields(configured_field: str) -> list[str]:
        """Return the configured field first, then the most common upstream alternatives."""
        ordered_fields = [configured_field, "query", "question"]
        deduplicated: list[str] = []
        for field_name in ordered_fields:
            normalized = field_name.strip()
            if normalized and normalized not in deduplicated:
                deduplicated.append(normalized)
        return deduplicated

    @staticmethod
    def _extract_missing_body_field_name(response: httpx.Response) -> str | None:
        """
        Read FastAPI/Pydantic 422 details and pull out a missing body field name when available.

        Example input/output:
        - Input: {"detail": [{"loc": ["body", "query"], "msg": "Field required"}]}
        - Output: "query"
        """
        payload = SecondBrainAdapter._safe_json(response)
        if not isinstance(payload, dict):
            return None
        detail = payload.get("detail")
        if not isinstance(detail, list):
            return None
        for entry in detail:
            if not isinstance(entry, dict):
                continue
            location = entry.get("loc")
            if isinstance(location, list) and len(location) >= 2 and location[0] == "body":
                field_name = location[1]
                if isinstance(field_name, str) and field_name.strip():
                    return field_name.strip()
        return None

    @staticmethod
    def _safe_json(response: httpx.Response) -> Any:
        """Return parsed JSON when possible and otherwise fall back to the raw response text."""
        try:
            return response.json()
        except ValueError:
            return response.text

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

        answer = self._sanitize_voice_answer(
            self._first_text(raw, "answer_preview", "answer", "summary", "result", "text", "message")
        )
        evidence = self._collect_evidence(raw)

        if not answer:
            contracts = raw.get("contracts")
            if isinstance(contracts, list) and contracts:
                answer = self._summarize_contracts(contracts)

        if not answer:
            semantic_results = raw.get("semantic_results")
            if isinstance(semantic_results, list) and semantic_results:
                answer = self._summarize_semantic_results(semantic_results)

        if not answer:
            items = (
                raw.get("items")
                or raw.get("results")
                or raw.get("documents")
                or raw.get("facts")
                or []
            )
            if isinstance(items, list) and items:
                answer = self._summarize_items(items)

        if not answer:
            return StructuredAnswer(
                status=ResultStatus.UNCERTAIN,
                source=SourceType.SECOND_BRAIN,
                answer="Ich habe Daten gefunden, aber noch keine kurze Sprachantwort daraus gebildet.",
                next_step="Prüfe die Upstream-Antwort in den Gateway-Debug-Logs.",
                evidence=evidence,
                raw=raw,
            )

        return StructuredAnswer(
            status=ResultStatus.OK,
            source=SourceType.SECOND_BRAIN,
            answer=answer.strip(),
            details=self._sanitize_voice_answer(self._first_text(raw, "details", "debug", "context")),
            next_step="Frage nach mehr Details, wenn du mehr wissen möchtest.",
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
    def _sanitize_voice_answer(text: str | None) -> str | None:
        """
        Why this exists: Some SecondBrain deployments return retrieval diagnostics such as match counters
        instead of a human-friendly spoken answer.
        What happens here: We remove those diagnostics early so the adapter can fall back to structured
        document summaries when needed.
        Example input/output:
        - Input: "Found 5 structured matches... limit 5. Zwei Vertraege enden bald."
        - Output: "Zwei Vertraege enden bald."
        """

        if not text or not text.strip():
            return None

        normalized = re.sub(r"\s+", " ", text).strip()
        fragments = [fragment.strip() for fragment in re.split(r"(?<=[.!?])\s+", normalized) if fragment.strip()]
        if not fragments:
            fragments = [normalized]

        filtered_fragments = [
            fragment
            for fragment in fragments
            if not any(pattern.search(fragment) for pattern in RETRIEVAL_DEBUG_PATTERNS)
        ]

        cleaned = " ".join(filtered_fragments).strip()
        return cleaned or None

    @staticmethod
    def _collect_evidence(payload: dict[str, Any]) -> list[EvidenceSnippet]:
        evidence: list[EvidenceSnippet] = []
        for key in ("sources", "documents", "facts", "semantic_results", "citations"):
            raw_items = payload.get(key)
            if not isinstance(raw_items, list):
                continue
            for item in raw_items[:3]:
                if isinstance(item, dict):
                    evidence.append(
                        EvidenceSnippet(
                            title=str(
                                item.get("title")
                                or item.get("document_title")
                                or item.get("name")
                                or item.get("counterparty")
                                or key.title()
                            ),
                            snippet=str(
                                item.get("snippet")
                                or item.get("summary")
                                or item.get("chunk_text")
                                or item.get("text")
                                or ""
                            )[:240],
                            url=item.get("url"),
                        )
                    )
        return evidence

    @staticmethod
    def _summarize_items(items: list[Any]) -> str:
        parts: list[str] = []
        for item in items[:3]:
            if isinstance(item, dict):
                label = item.get("friendly_name") or item.get("title") or item.get("name") or item.get("id") or "Treffer"
                value = (
                    item.get("paperless_note_summary")
                    or item.get("summary")
                    or item.get("text")
                    or item.get("status")
                    or "gefunden"
                )
                parts.append(f"{label}: {value}")
            else:
                parts.append(str(item))
        return "Ich habe passende Treffer gefunden. " + " ".join(parts)

    @staticmethod
    def _summarize_contracts(contracts: list[Any]) -> str:
        """
        Why this exists: The real SecondBrain `/query` endpoint can return structured contract objects instead of
        a ready-made answer string.
        What happens here: We turn the top contract matches into short, spoken summaries with grounded dates/status.
        Example input/output:
        - Input: [{"counterparty": "ERGO", "end_date": "2024-10-23", "status": "expired"}]
        - Output: "Ich habe Vertragsdaten gefunden. ERGO endete am 2024-10-23."
        """

        parts: list[str] = []
        for contract in contracts[:3]:
            if not isinstance(contract, dict):
                continue

            counterparty = str(contract.get("counterparty") or contract.get("document_title") or "Unknown contract")
            status = str(contract.get("status") or "").strip().lower()
            end_date = str(contract.get("end_date") or contract.get("renewal_date") or "").strip()

            if end_date and status:
                parts.append(f"{counterparty} ist {status} mit Datum {end_date}")
            elif end_date:
                parts.append(f"{counterparty} hat das Datum {end_date}")
            elif status:
                parts.append(f"{counterparty} ist {status}")
            else:
                parts.append(counterparty)

        if not parts:
            return ""

        lead = "Ich habe Vertragsdaten gefunden."
        return lead + " " + ". ".join(parts) + "."

    @staticmethod
    def _summarize_semantic_results(results: list[Any]) -> str:
        """Summarize top semantic matches when the upstream response has no direct answer string."""
        parts: list[str] = []
        for result in results[:3]:
            if not isinstance(result, dict):
                continue
            title = str(result.get("document_title") or result.get("title") or "Dokumenttreffer")
            summary = (
                result.get("paperless_note_summary")
                or result.get("summary")
                or result.get("snippet")
                or ""
            )
            snippet = str(summary or result.get("chunk_text") or "").strip().replace("\n", " ")
            if snippet:
                parts.append(f"{title}: {snippet[:120].rstrip()}")
                continue

            counterparty = result.get("counterparty")
            created_date = result.get("created_date") or result.get("created")
            if counterparty and created_date:
                parts.append(f"{counterparty} vom {created_date}")
            else:
                parts.append(title)

        if not parts:
            return ""

        return "Ich habe passende Dokumente gefunden. " + " ".join(parts) + "."
