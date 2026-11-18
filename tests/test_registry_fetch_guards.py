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

    def fake(req, timeout=None, context=None):
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
    assert reg._cache_path(tmp_path, _registry()).read_text(encoding="utf-8") == raw


# --- TLS trust roots in frozen builds ----------------------------------------------------


class _FakeCtx:
    def __init__(self):
        self.loaded: list[str] = []

    def load_verify_locations(self, cafile=None, **_):
        self.loaded.append(cafile)


def _ctx_env(monkeypatch, *, cafile, capath, bundles, env=None):
    import ssl

    ctx = _FakeCtx()
    monkeypatch.setattr(reg.ssl, "create_default_context", lambda: ctx)
    monkeypatch.setattr(
        reg.ssl,
        "get_default_verify_paths",
        lambda: ssl.DefaultVerifyPaths(cafile, capath, "SSL_CERT_FILE", "", "SSL_CERT_DIR", ""),
    )
    monkeypatch.setattr(reg, "SYSTEM_CA_FILES", tuple(str(b) for b in bundles))
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)
    return ctx


def test_ssl_context_falls_back_to_os_bundle_when_baked_path_missing(tmp_path, monkeypatch):
    # A PyInstaller build's OpenSSL points at the build machine's dir; on the user's machine
    # it's absent, so the first existing OS bundle must be loaded (skipping missing ones).
    present = tmp_path / "cert.pem"
    present.write_text("x")
    ctx = _ctx_env(
        monkeypatch, cafile=None, capath=None, bundles=[tmp_path / "missing.pem", present]
    )
    assert reg._ssl_context() is ctx
    assert ctx.loaded == [str(present)]


def test_ssl_context_keeps_working_defaults(tmp_path, monkeypatch):
    present = tmp_path / "cert.pem"
    present.write_text("x")
    ctx = _ctx_env(monkeypatch, cafile=str(present), capath=None, bundles=[present])
    reg._ssl_context()
    assert ctx.loaded == []


def test_ssl_context_respects_ssl_cert_file(tmp_path, monkeypatch):
    present = tmp_path / "cert.pem"
    present.write_text("x")
    ctx = _ctx_env(
        monkeypatch, cafile=None, capath=None, bundles=[present], env={"SSL_CERT_FILE": "/x"}
    )
    reg._ssl_context()
    assert ctx.loaded == []


def test_fetch_uses_verifying_context(tmp_path, monkeypatch):
    seen: dict[str, object] = {}

    class _Resp(io.BytesIO):
        url = "https://example.invalid/c.json"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake(req, timeout=None, context=None):
        seen["context"] = context
        return _Resp(b'{"repositories": {}}')

    monkeypatch.setattr(reg.urllib.request, "urlopen", fake)
    reg._load_raw(tmp_path, _registry())
    import ssl

    ctx = seen["context"]
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
