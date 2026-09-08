from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest
import requests


BACKEND_ROOT = Path(__file__).resolve().parents[1]
backend_root_str = str(BACKEND_ROOT)
if backend_root_str not in sys.path:
    sys.path.insert(0, backend_root_str)


@pytest.fixture(autouse=True)
def isolate_external_io(monkeypatch, tmp_path):
    from app import qa_logging

    def block_network(*args, **kwargs):
        pytest.fail("Offline tests must mock network access; use qa_eval for live model checks")

    # Block external HTTP, not asyncio's internal socketpair on Windows.
    # MockTransport and in-process ASGI clients remain usable.
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", block_network)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", block_network)
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", block_network)
    monkeypatch.setattr(qa_logging, "_question_log_dir", lambda: tmp_path)
