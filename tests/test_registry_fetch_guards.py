"""Guards on the catalog HTTP fetch: timeout, size cap, redirect scheme.

``agy init`` registers a remote catalog by default, so every ``add``/``list``/``sync`` can
touch the network. These tests use a fake ``urlopen`` — the suite stays offline.
"""

from __future__ import annotations

import io
from contextlib import contextmanager

import pytest

from agentry import registry as reg
from agentry.models import Registry


def _registry():
    return Registry(name="c", location="https://example.invalid/catalog.json")


@contextmanager
def _fake_urlopen(monkeypatch, *, body: bytes, final_url: str = "https://example.invalid/c.json"):
    calls: dict[str, object] = {}

    class _Resp(io.BytesIO):
        url = final_url

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake(req, timeout=None):
        calls["timeout"] = timeout
        return _Resp(body)

    monkeypatch.setattr(reg.urllib.request, "urlopen", fake)
    yield calls


def test_fetch_passes_a_timeout(tmp_path, monkeypatch):
    with _fake_urlopen(monkeypatch, body=b'{"repositories": {}}') as calls:
        reg._load_raw(tmp_path, _registry())
    assert calls["timeout"] == reg.FETCH_TIMEOUT


def test_oversize_response_is_refused_and_not_cached(tmp_path, monkeypatch):
    body = b"x" * (reg.MAX_CATALOG_BYTES + 1)
    with (
        _fake_urlopen(monkeypatch, body=body),
        pytest.raises(reg.RegistryError, match="exceeds"),
    ):
        reg._load_raw(tmp_path, _registry())
    assert not reg._cache_path(tmp_path, _registry()).exists()


def test_redirect_to_a_non_http_scheme_is_refused(tmp_path, monkeypatch):
    with (
        _fake_urlopen(monkeypatch, body=b"{}", final_url="file:///etc/passwd"),
        pytest.raises(reg.RegistryError, match="refused redirect"),
    ):
        reg._load_raw(tmp_path, _registry())


def test_successful_fetch_is_cached_in_the_store(tmp_path, monkeypatch):
    with _fake_urlopen(monkeypatch, body=b'{"repositories": {}}'):
        raw = reg._load_raw(tmp_path, _registry())
    assert raw == '{"repositories": {}}'
    assert reg._cache_path(tmp_path, _registry()).read_text() == raw
