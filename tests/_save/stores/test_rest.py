# Copyright 2026 Marimo. All rights reserved.
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from unittest.mock import MagicMock, patch

from marimo._save.cache import Cache
from marimo._save.hash import HashKey
from marimo._save.loaders.json import JsonLoader
from marimo._save.stores import rest


def test_get_url_encodes_cache_key_segments() -> None:
    store = rest.RestStore(
        base_url="https://cache.example.com", api_key="test-key"
    )

    assert (
        store._get_url("my cache/P_repro.json")
        == "https://cache.example.com/my%20cache/P_repro.json"
    )
    # An already-escaped key must not encode twice.
    assert (
        store._get_url("my%20cache/P_repro.json")
        == "https://cache.example.com/my%20cache/P_repro.json"
    )
    # Ordinary keys are unchanged.
    assert store._get_url("cache-key") == "https://cache.example.com/cache-key"


def test_spaced_cache_name_restores_over_rest() -> None:
    blob = JsonLoader("payload").to_blob(
        Cache(
            defs={"answer": 42},
            hash="repro",
            cache_type="Pure",
            stateful_refs=set(),
            hit=False,
            meta={},
        )
    )
    requests: list[tuple[str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            requests.append(("GET", self.path))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(blob)

        def do_HEAD(self) -> None:
            requests.append(("HEAD", self.path))
            self.send_response(200)
            self.end_headers()

        def do_PUT(self) -> None:
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)
            requests.append(("PUT", self.path))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        store = rest.RestStore(
            base_url=f"http://127.0.0.1:{server.server_port}",
            api_key="test-key",
        )
        loader = JsonLoader("my cache", store=store)
        key = HashKey(hash="repro", cache_type="Pure")
        entry = str(loader.build_path(key))

        restored = loader.load_cache(key)
        assert restored is not None
        assert restored.defs == {"answer": 42}
        assert store.hit(entry) is True
        assert store.read(entry) == blob
        assert store.put(entry, blob) is True
        # An already-escaped key reaches the same entry.
        assert store.get("my%20cache/P_repro.json") == blob
    finally:
        server.shutdown()
        server.server_close()
        thread.join()

    assert requests
    assert all(path == "/my%20cache/P_repro.json" for _, path in requests)


def test_get_logs_unexpected_response_status() -> None:
    store = rest.RestStore(
        base_url="https://cache.example.com", api_key="test-key"
    )
    response = MagicMock()
    response.status = 500
    response.__enter__.return_value = response

    with (
        patch.object(rest.urllib.request, "urlopen", return_value=response),
        patch.object(rest.LOGGER, "warning") as warning,
    ):
        result = store.get("cache-key")

    assert result is None
    warning.assert_called_once_with(
        "GET %s - Unexpected status: %s",
        "https://cache.example.com/cache-key",
        500,
    )
