"""
Purpose: Shared pytest helpers and import path setup for the gateway test suite.
Input/Output: Pytest imports this file automatically before running individual tests.
Invariants: Tests can import the `src` package layout without requiring an editable install first.
Debugging: If imports fail, confirm the repository root and `src` path calculation below.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture
def patch_async_client(monkeypatch):
    """
    Purpose: Replace `httpx.AsyncClient` inside one module with a deterministic mock transport.
    Input/Output: Tests pass the target module and one request handler function.
    Invariants: Only the chosen module is patched, which keeps test isolation easy to reason about.
    Debugging: If a real network call still happens, verify you patched the same module that imports `httpx`.
    """

    def _patch(target_module, handler):
        transport = httpx.MockTransport(handler)
        original_async_client = target_module.httpx.AsyncClient

        class PatchedAsyncClient:
            def __init__(self, *args, **kwargs):
                kwargs["transport"] = transport
                self._client = original_async_client(*args, **kwargs)

            async def __aenter__(self):
                return await self._client.__aenter__()

            async def __aexit__(self, exc_type, exc, tb):
                return await self._client.__aexit__(exc_type, exc, tb)

            def __getattr__(self, item):
                return getattr(self._client, item)

        monkeypatch.setattr(target_module.httpx, "AsyncClient", PatchedAsyncClient)

    return _patch
