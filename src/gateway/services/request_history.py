"""
Purpose: Persist a privacy-aware JSONL history of Alexa and local debug requests for later routing improvements.
Input/Output: Accepts already-sanitized event dictionaries and appends them to one daily history file.
Invariants: Sensitive Alexa account tokens are never written; write failures must never break a live voice response.
Debugging: Set `REQUEST_HISTORY_ENABLED=true` and inspect the JSONL files under `REQUEST_HISTORY_DIR`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from gateway.config import Settings

logger = logging.getLogger(__name__)


class RequestHistoryRecorder:
    """
    Purpose: Keep request-history persistence isolated from API and routing code.
    Input/Output: `record_event()` takes one structured dictionary and appends it as JSONL.
    Invariants: Recording is best-effort only and must never block the main Alexa flow with a fatal exception.
    Debugging: Watch WARN logs when the target path is not writable or the JSON payload is malformed.
    """

    def __init__(self, settings: Settings) -> None:
        self.enabled = settings.request_history_enabled
        self.directory = settings.request_history_dir if settings.request_history_dir.is_absolute() else Path.cwd() / settings.request_history_dir
        self.include_answers = settings.request_history_include_answers
        self.max_answer_chars = max(200, settings.request_history_max_answer_chars)
        self._lock = asyncio.Lock()

        if self.enabled:
            logger.info("Request history recording is enabled at %s", self.directory)

    async def record_event(self, event: dict[str, Any]) -> Path | None:
        """
        Why this exists: Real Alexa usage teaches us which utterances route badly and which answers sound awkward.
        What happens here: We enrich one safe event with a server timestamp and append it to the daily JSONL file.
        Example input/output:
        - Input: {"event_type": "alexa_question", "request": {"question": "frage chatgpt ..."}}
        - Output: `/app/data/request_history/2026-04-03.jsonl` gains one new JSON line.
        """

        if not self.enabled:
            return None

        payload = dict(event)
        payload.setdefault("recorded_at", datetime.now(UTC).isoformat())
        payload.setdefault("schema_version", 1)
        payload = self._trim_large_text_fields(payload)

        async with self._lock:
            try:
                target = self._target_file(payload["recorded_at"])
                target.parent.mkdir(parents=True, exist_ok=True)
                line = json.dumps(payload, ensure_ascii=False, sort_keys=True)
                await asyncio.to_thread(self._append_line, target, line)
                return target
            except Exception as exc:  # noqa: BLE001 - request history must stay best-effort.
                logger.warning("Could not write request history entry: %s", exc)
                return None

    @staticmethod
    def _append_line(target: Path, line: str) -> None:
        with target.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.write("\n")

    def _target_file(self, recorded_at: str) -> Path:
        date_part = recorded_at.split("T", 1)[0]
        return self.directory / f"{date_part}.jsonl"

    def _trim_large_text_fields(self, payload: dict[str, Any]) -> dict[str, Any]:
        """
        Keep history files readable and bounded even when one backend returns a long answer.

        Example input/output:
        - Input: {"response": {"answer": "<5000 chars>"}}
        - Output: the answer is shortened and marked as truncated.
        """

        response = payload.get("response")
        if not isinstance(response, dict):
            return payload

        if not self.include_answers:
            response.pop("answer", None)
            response.pop("spoken_text", None)
            payload["response"] = response
            return payload

        for key in ("answer", "spoken_text", "card_text", "reprompt_text"):
            value = response.get(key)
            if isinstance(value, str) and len(value) > self.max_answer_chars:
                response[key] = value[: self.max_answer_chars].rstrip() + " …[truncated]"

        payload["response"] = response
        return payload
