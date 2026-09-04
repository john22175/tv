from __future__ import annotations

import html
import hashlib
import json
import mimetypes
import socket
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlparse
import re

DEFAULT_MEDIA_SERVER_PORT = 65331


@dataclass(slots=True)
class ServedMedia:
    media_id: str
    url: str
    mime_type: str
    path: Path


@dataclass(slots=True)
class ReceiverState:
    receiver_id: str
    receiver_alias: str = ""
    source_name: str = "Waiting for media"
    mime_type: str = "text/plain"
    media_url: str | None = None
    note: str = "Receiver is ready. Send or drop a media source from the desktop app."
    playback_state: str = "idle"
    start_position_seconds: float = 0.0
    playback_token: int = 0
    stage_session_id: str = ""
    library_item_id: str = ""
    library_content_hash: str = ""
    updated_at: float = 0.0
    last_seen_at: float = 0.0


@dataclass(slots=True, frozen=True)
class OfflineLibrarySource:
    """One desktop source that the receiver app may save for offline use."""

    item_id: str
    name: str
    mime_type: str
    size: int
    content_hash: str
    path: Path
    playable: bool


@dataclass(slots=True)
class LibrarySyncStatus:
    request_id: str
    state: str = "pending"
    revision: str = ""
    detail: str = "Waiting for the receiver app to check in."
    stored_bytes: int = 0
    updated_at: float = 0.0


class MediaRegistry:
    def __init__(self) -> None:
        self._items: dict[str, Path] = {}
        self._lock = threading.Lock()

    def register(self, path: Path) -> str:
        media_id = uuid.uuid4().hex
        with self._lock:
            self._items[media_id] = path
        return media_id

    def resolve(self, media_id: str) -> Path | None:
        with self._lock:
            return self._items.get(media_id)


class ReceiverRegistry:
    def __init__(self) -> None:
        self._items: dict[str, ReceiverState] = {}
        self._aliases: dict[str, str] = {}
        self._aliases_reverse: dict[str, str] = {}
        self._hosts: dict[str, str] = {}
        self._next_alias_id = 1
        self._lock = threading.Lock()
        self._seen = threading.Condition(self._lock)

    def ensure(
        self,
        receiver_id: str,
        preferred_alias: str | None = None,
        preferred_host: str | None = None,
    ) -> ReceiverState:
        with self._lock:
            state = self._items.get(receiver_id)
            if state is None:
                state = ReceiverState(receiver_id=receiver_id, updated_at=time.time())
                self._items[receiver_id] = state
            self._ensure_alias(receiver_id, preferred_alias)
            self._register_host(receiver_id, preferred_host)
            return state

    def update(
        self,
        receiver_id: str,
        *,
        source_name: str,
        mime_type: str,
        media_url: str | None,
        note: str,
        playback_state: str = "playing",
        start_position_seconds: float = 0.0,
        playback_token: int | None = None,
        stage_session_id: str | None = None,
        library_item_id: str | None = None,
        library_content_hash: str | None = None,
        preferred_host: str | None = None,
    ) -> ReceiverState:
        with self._lock:
            state = self._items.get(receiver_id)
            if state is None:
                state = ReceiverState(receiver_id=receiver_id)
                self._items[receiver_id] = state
            state.source_name = source_name
            state.mime_type = mime_type
            state.media_url = media_url
            state.note = note
            state.playback_state = playback_state
            state.start_position_seconds = max(0.0, float(start_position_seconds))
            if playback_token is not None:
                state.playback_token = int(playback_token)
            if stage_session_id is not None:
                state.stage_session_id = str(stage_session_id)
            if library_item_id is not None:
                state.library_item_id = str(library_item_id)
            if library_content_hash is not None:
                state.library_content_hash = str(library_content_hash)
            state.updated_at = time.time()
            self._register_host(receiver_id, preferred_host)
            return state

    def set_playback_state(
        self,
        receiver_id: str,
        *,
        playback_state: str,
        start_position_seconds: float | None = None,
        playback_token: int | None = None,
        preferred_host: str | None = None,
    ) -> ReceiverState:
        with self._lock:
            state = self._items.get(receiver_id)
            if state is None:
                state = ReceiverState(receiver_id=receiver_id)
                self._items[receiver_id] = state
            state.playback_state = playback_state
            if start_position_seconds is not None:
                state.start_position_seconds = max(0.0, float(start_position_seconds))
            if playback_token is not None:
                state.playback_token = int(playback_token)
            state.updated_at = time.time()
            self._register_host(receiver_id, preferred_host)
            return state

    def start_stage_session(self, stage_session_id: str) -> int:
        """Start every receiver staged in one session, retaining each receiver's offset."""
        with self._lock:
            started = 0
            for state in self._items.values():
                if state.stage_session_id != stage_session_id:
                    continue
                state.playback_state = "playing"
                state.playback_token += 1
                state.updated_at = time.time()
                started += 1
            return started

    def get(self, receiver_id: str) -> ReceiverState | None:
        with self._lock:
            return self._items.get(receiver_id)

    def mark_seen(self, receiver_id: str) -> None:
        with self._seen:
            state = self._items.get(receiver_id)
            if state is None:
                state = ReceiverState(receiver_id=receiver_id)
                self._items[receiver_id] = state
            state.last_seen_at = time.time()
            self._seen.notify_all()

    def wait_until_seen(self, receiver_id: str, *, after: float, timeout: float) -> bool:
        deadline = time.monotonic() + max(0.0, timeout)
        with self._seen:
            while True:
                state = self._items.get(receiver_id)
                if state is not None and state.last_seen_at >= after:
                    return True
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    return False
                self._seen.wait(remaining)

    def alias_for(self, receiver_id: str, preferred_alias: str | None = None) -> str:
        with self._lock:
            self._ensure_alias(receiver_id, preferred_alias)
            return self._aliases[receiver_id]

    def resolve_alias(self, alias: str) -> str | None:
        with self._lock:
            return self._aliases_reverse.get(alias)

    def resolve_request(self, alias: str, requester_host: str | None) -> str | None:
        with self._lock:
            normalized_host = self._normalize_host(requester_host)
            if normalized_host:
                receiver_id = self._hosts.get(normalized_host)
                if receiver_id:
                    return receiver_id
            return self._aliases_reverse.get(alias)

    def resolve_request_by_host(self, requester_host: str | None) -> str | None:
        with self._lock:
            normalized_host = self._normalize_host(requester_host)
            if not normalized_host:
                return None
            return self._hosts.get(normalized_host)

    def list_entries(self) -> list[tuple[str, ReceiverState]]:
        with self._lock:
            items: list[tuple[str, ReceiverState]] = []
            for receiver_id, state in self._items.items():
                alias = self._aliases.get(receiver_id)
                if alias:
                    items.append((alias, state))
            items.sort(key=lambda item: item[0])
            return items

    def _ensure_alias(self, receiver_id: str, preferred_alias: str | None) -> None:
        existing = self._aliases.get(receiver_id)
        if existing:
            state = self._items.get(receiver_id)
            if state is not None and state.receiver_alias != existing:
                state.receiver_alias = existing
            return

        normalized = self._normalize_alias(preferred_alias)
        if normalized and normalized not in self._aliases_reverse:
            alias = normalized
        else:
            alias = self._generate_alias()

        self._aliases[receiver_id] = alias
        self._aliases_reverse[alias] = receiver_id
        state = self._items.get(receiver_id)
        if state is not None:
            state.receiver_alias = alias

    def _register_host(self, receiver_id: str, preferred_host: str | None) -> None:
        normalized_host = self._normalize_host(preferred_host)
        if normalized_host:
            self._hosts[normalized_host] = receiver_id

    def _normalize_alias(self, preferred_alias: str | None) -> str | None:
        if not preferred_alias:
            return None
        alias = re.sub(r"[^a-z0-9]+", "", preferred_alias.lower())[:8]
        return alias or None

    def _normalize_host(self, preferred_host: str | None) -> str | None:
        host = str(preferred_host or "").strip()
        return host or None

    def _generate_alias(self) -> str:
        while True:
            alias = _base36(self._next_alias_id)
            self._next_alias_id += 1
            if alias not in self._aliases_reverse:
                return alias


class MediaHTTPServer:
    def __init__(self, host: str = "0.0.0.0", port: int = DEFAULT_MEDIA_SERVER_PORT) -> None:
        self._registry = MediaRegistry()
        self._receivers = ReceiverRegistry()
        self._library_lock = threading.Lock()
        self._library_sources: dict[str, tuple[OfflineLibrarySource, str]] = {}
        self._library_revision = ""
        self._library_requests: dict[str, str] = {}
        self._library_status: dict[str, LibrarySyncStatus] = {}
        # Ephemeral port instances are used by the test suite and should not
        # pollute the operator-visible transfer history.
        self._offline_library_log_path: Path | None = (
            None if port == 0 else Path(__file__).resolve().parent.parent / "logs" / "receiver_source_transfer.log"
        )
        self._offline_library_log_lock = threading.Lock()
        self._server = ThreadingHTTPServer((host, port), self._handler())
        self._thread: threading.Thread | None = None
        self._lan_ip = self._detect_lan_ip()

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        registry = self._registry
        receivers = self._receivers
        media_server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                self._handle_request(send_body=True)

            def do_HEAD(self) -> None:
                self._handle_request(send_body=False)

            def do_OPTIONS(self) -> None:
                self.send_response(HTTPStatus.NO_CONTENT)
                self._send_common_headers()
                self.end_headers()

            def do_POST(self) -> None:
                parsed = urlparse(self.path)
                path = parsed.path.strip("/")
                parts = path.split("/") if path else []
                if len(parts) == 2 and parts[0] == "receiver-stage-play":
                    session_id = parts[1].strip()
                    if not session_id:
                        self.send_error(HTTPStatus.BAD_REQUEST)
                        return
                    started = receivers.start_stage_session(session_id)
                    body = json.dumps({"started": started}).encode("utf-8")
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self._send_common_headers(cache=False)
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if self.path == "/receiver-library-status":
                    self._record_library_status(receivers, media_server)
                    return
                if self.path != "/internal/receiver-source" or self.client_address[0] not in {"127.0.0.1", "::1"}:
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                    receiver_id = str(payload["receiver_id"])
                    source_path = Path(str(payload["source_path"])).resolve()
                    preferred_host = str(payload.get("preferred_host") or "").strip() or None
                    preferred_alias = str(payload.get("preferred_alias") or "").strip() or None
                except (KeyError, OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
                    self.send_error(HTTPStatus.BAD_REQUEST, "Expected receiver_id and source_path JSON fields.")
                    return
                if not source_path.is_file():
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return

                receivers.ensure(receiver_id, preferred_alias=preferred_alias, preferred_host=preferred_host)

                media_id = registry.register(source_path)
                mime_type = mimetypes.guess_type(source_path.name)[0] or "application/octet-stream"
                media_url = f"{media_server.base_url}/media/{media_id}/{quote(source_path.name)}"
                receivers.update(
                    receiver_id,
                    source_name=source_path.name,
                    mime_type=mime_type,
                    media_url=media_url,
                    note="Source sent through the local MultiHub diagnostic route.",
                    playback_state="playing",
                    start_position_seconds=0.0,
                    preferred_host=preferred_host,
                )
                body = json.dumps({"receiver_id": receiver_id, "media_url": media_url}).encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self._send_common_headers(cache=False)
                self.end_headers()
                self.wfile.write(body)

            def _handle_request(self, *, send_body: bool) -> None:
                parsed = urlparse(self.path)
                path = parsed.path.strip("/")
                parts = path.split("/") if path else []
                requester_host = self.client_address[0] if self.client_address else None
                if not parts or parts == [""]:
                    self._serve_receiver_hub(receivers, send_body=send_body)
                    return
                if len(parts) >= 2 and parts[0] == "media":
                    self._serve_media(parts[1], registry, receivers, media_server, send_body=send_body)
                    return
                if len(parts) >= 2 and parts[0] == "receiver":
                    self._serve_receiver_page(parts[1], receivers, send_body=send_body)
                    return
                if len(parts) >= 2 and parts[0] == "r":
                    receiver_id = receivers.resolve_request(parts[1], requester_host)
                    if receiver_id is None:
                        self.send_error(HTTPStatus.NOT_FOUND)
                        return
                    self._serve_receiver_page(receiver_id, receivers, send_body=send_body)
                    return
                if len(parts) >= 2 and parts[0] == "receiver-state":
                    self._serve_receiver_state(parts[1], receivers, requester_host=requester_host, send_body=send_body)
                    return
                if parts == ["receiver-state-current"]:
                    receiver_id = receivers.resolve_request_by_host(requester_host)
                    if receiver_id is None:
                        self.send_error(HTTPStatus.NOT_FOUND)
                        return
                    self._serve_receiver_state(receiver_id, receivers, requester_host=requester_host, send_body=send_body)
                    return
                if parts == ["receiver-library-current"]:
                    receiver_id = receivers.resolve_request_by_host(requester_host)
                    if receiver_id is None:
                        self.send_error(HTTPStatus.NOT_FOUND)
                        return
                    self._serve_receiver_library(receiver_id, receivers, media_server, send_body=send_body)
                    return
                if len(parts) >= 2 and parts[0] == "receiver-state-alias":
                    receiver_id = receivers.resolve_request(parts[1], requester_host)
                    if receiver_id is None:
                        self.send_error(HTTPStatus.NOT_FOUND)
                        return
                    self._serve_receiver_state(receiver_id, receivers, requester_host=requester_host, send_body=send_body)
                    return
                if len(parts) >= 2 and parts[0] == "receiver-library-alias":
                    receiver_id = receivers.resolve_request(parts[1], requester_host)
                    if receiver_id is None:
                        self.send_error(HTTPStatus.NOT_FOUND)
                        return
                    self._serve_receiver_library(receiver_id, receivers, media_server, send_body=send_body)
                    return
                self.send_error(HTTPStatus.NOT_FOUND)

            def _serve_media(
                self,
                media_id: str,
                media_registry: MediaRegistry,
                receiver_registry: ReceiverRegistry,
                server: "MediaHTTPServer",
                *,
                send_body: bool,
            ) -> None:
                media_path = media_registry.resolve(media_id)
                if media_path is None or not media_path.exists():
                    server.log_offline_library_event(
                        "media_not_found",
                        client_host=self.client_address[0] if self.client_address else "",
                        media_id=media_id,
                    )
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return

                mime_type = mimetypes.guess_type(media_path.name)[0] or "application/octet-stream"
                file_size = media_path.stat().st_size
                range_header = self.headers.get("Range")
                byte_range = _parse_range_header(range_header, file_size)
                start = 0
                end = file_size - 1
                status = HTTPStatus.OK
                if range_header:
                    if byte_range is None:
                        self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                        self.send_header("Content-Range", f"bytes */{file_size}")
                        self.end_headers()
                        return
                    start, end = byte_range
                    status = HTTPStatus.PARTIAL_CONTENT

                content_length = max(0, (end - start) + 1)
                client_host = self.client_address[0] if self.client_address else ""
                receiver_id = receiver_registry.resolve_request_by_host(client_host) or ""
                server.log_offline_library_event(
                    "media_transfer_started",
                    client_host=client_host,
                    receiver_id=receiver_id,
                    media_id=media_id,
                    source_name=media_path.name,
                    source_size=file_size,
                    range_start=start,
                    range_end=end,
                    requested_bytes=content_length,
                    http_status=int(status),
                )
                self.send_response(status)
                self.send_header("Content-Type", mime_type)
                self.send_header("Content-Length", str(content_length))
                self.send_header("Accept-Ranges", "bytes")
                if status == HTTPStatus.PARTIAL_CONTENT:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
                self._send_common_headers()
                self.end_headers()
                if not send_body:
                    server.log_offline_library_event(
                        "media_transfer_headers_sent",
                        client_host=client_host,
                        receiver_id=receiver_id,
                        source_name=media_path.name,
                        requested_bytes=content_length,
                    )
                    return
                transferred = 0
                try:
                    with media_path.open("rb") as media_file:
                        media_file.seek(start)
                        remaining = content_length
                        while remaining > 0:
                            chunk = media_file.read(min(64 * 1024, remaining))
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            remaining -= len(chunk)
                            transferred += len(chunk)
                except (BrokenPipeError, ConnectionResetError, OSError) as exc:
                    server.log_offline_library_event(
                        "media_transfer_interrupted",
                        client_host=client_host,
                        receiver_id=receiver_id,
                        source_name=media_path.name,
                        transferred_bytes=transferred,
                        requested_bytes=content_length,
                        detail=str(exc),
                    )
                    return
                server.log_offline_library_event(
                    "media_transfer_completed",
                    client_host=client_host,
                    receiver_id=receiver_id,
                    source_name=media_path.name,
                    transferred_bytes=transferred,
                    requested_bytes=content_length,
                )

            def _serve_receiver_page(self, receiver_id: str, receiver_registry: ReceiverRegistry, *, send_body: bool) -> None:
                state = receiver_registry.ensure(receiver_id)
                alias = receiver_registry.alias_for(receiver_id)
                body = _receiver_html(receiver_id=receiver_id, receiver_alias=alias, source_name=state.source_name)
                encoded = body.encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(encoded)))
                self._send_common_headers(cache=False)
                self.end_headers()
                if send_body:
                    self.wfile.write(encoded)

            def _serve_receiver_hub(self, receiver_registry: ReceiverRegistry, *, send_body: bool) -> None:
                body = _receiver_hub_html(receiver_registry.list_entries())
                encoded = body.encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(encoded)))
                self._send_common_headers(cache=False)
                self.end_headers()
                if send_body:
                    self.wfile.write(encoded)

            def _serve_receiver_state(
                self,
                receiver_id: str,
                receiver_registry: ReceiverRegistry,
                *,
                requester_host: str | None,
                send_body: bool,
            ) -> None:
                state = receiver_registry.ensure(receiver_id)
                if receiver_registry.resolve_request_by_host(requester_host) == receiver_id:
                    receiver_registry.mark_seen(receiver_id)
                body = json.dumps(asdict(state)).encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self._send_common_headers(cache=False)
                self.end_headers()
                if send_body:
                    self.wfile.write(body)

            def _serve_receiver_library(
                self,
                receiver_id: str,
                receiver_registry: ReceiverRegistry,
                server: "MediaHTTPServer",
                *,
                send_body: bool,
            ) -> None:
                receiver_registry.mark_seen(receiver_id)
                manifest = server.receiver_library_manifest(receiver_id)
                server.log_offline_library_event(
                    "library_manifest_served",
                    client_host=self.client_address[0] if self.client_address else "",
                    receiver_id=receiver_id,
                    request_id=str(manifest.get("request_id") or ""),
                    revision=str(manifest.get("revision") or ""),
                    entry_count=len(manifest.get("entries") or []),
                )
                body = json.dumps(manifest).encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self._send_common_headers(cache=False)
                self.end_headers()
                if send_body:
                    self.wfile.write(body)

            def _record_library_status(self, receiver_registry: ReceiverRegistry, server: "MediaHTTPServer") -> None:
                requester_host = self.client_address[0] if self.client_address else None
                receiver_id = receiver_registry.resolve_request_by_host(requester_host)
                if receiver_id is None:
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                    state = str(payload.get("state") or "").strip().lower()
                    request_id = str(payload.get("request_id") or "").strip()
                    revision = str(payload.get("revision") or "").strip()
                    detail = str(payload.get("detail") or "").strip()
                    stored_bytes = max(0, int(payload.get("stored_bytes") or 0))
                except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
                    self.send_error(HTTPStatus.BAD_REQUEST, "Expected a library sync status JSON body.")
                    return
                if state not in {"syncing", "synced", "failed", "up_to_date"}:
                    self.send_error(HTTPStatus.BAD_REQUEST, "Unsupported library sync state.")
                    return
                if not server.record_receiver_library_status(
                    receiver_id,
                    request_id=request_id,
                    state=state,
                    revision=revision,
                    detail=detail,
                    stored_bytes=stored_bytes,
                ):
                    self.send_error(HTTPStatus.CONFLICT, "Library sync request is no longer current.")
                    return
                server.log_offline_library_event(
                    "library_status_reported",
                    client_host=requester_host or "",
                    receiver_id=receiver_id,
                    request_id=request_id,
                    revision=revision,
                    state=state,
                    stored_bytes=stored_bytes,
                    detail=detail,
                )
                receiver_registry.mark_seen(receiver_id)
                body = b'{"ok":true}'
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self._send_common_headers(cache=False)
                self.end_headers()
                self.wfile.write(body)

            def _send_common_headers(self, *, cache: bool = True) -> None:
                if cache:
                    self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
                    self.send_header("Pragma", "no-cache")
                    self.send_header("Expires", "0")
                else:
                    self.send_header("Cache-Control", "no-store")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, HEAD, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Range, Content-Type")
                self.send_header("Cross-Origin-Resource-Policy", "cross-origin")

            def log_message(self, format: str, *args: object) -> None:
                return

        return Handler

    def _detect_lan_ip(self) -> str:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
        except OSError:
            return "127.0.0.1"
        finally:
            sock.close()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self.log_offline_library_event("media_server_started", base_url=self.base_url)

    def stop(self) -> None:
        self.log_offline_library_event("media_server_stopped")
        self._server.shutdown()
        self._server.server_close()

    @property
    def port(self) -> int:
        return int(self._server.server_port)

    @property
    def base_url(self) -> str:
        return f"http://{self._lan_ip}:{self.port}"

    @property
    def hub_url(self) -> str:
        return self.base_url

    @property
    def offline_library_log_path(self) -> Path | None:
        return self._offline_library_log_path

    def log_offline_library_event(self, event: str, **details: object) -> None:
        """Append one JSON record for each offline-library transfer event."""
        if self._offline_library_log_path is None:
            return
        record = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "event": event,
            **details,
        }
        try:
            encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, default=str)
            with self._offline_library_log_lock:
                self._offline_library_log_path.parent.mkdir(parents=True, exist_ok=True)
                with self._offline_library_log_path.open("a", encoding="utf-8") as log_file:
                    log_file.write(encoded + "\n")
        except OSError:
            # Diagnostics must never interrupt media delivery.
            pass

    def publish(self, path: Path) -> ServedMedia:
        media_id = self._registry.register(path)
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        safe_name = quote(path.name)
        url = f"{self.base_url}/media/{media_id}/{safe_name}"
        return ServedMedia(media_id=media_id, url=url, mime_type=mime_type, path=path)

    def set_offline_library(self, sources: list[OfflineLibrarySource]) -> str:
        """Publish one immutable-at-a-time source catalog for all Tizen receivers."""
        prepared: dict[str, tuple[OfflineLibrarySource, str]] = {}
        canonical_entries: list[dict[str, object]] = []
        for source in sorted(sources, key=lambda item: item.item_id.casefold()):
            media_id = self._registry.register(source.path)
            prepared[source.item_id] = (source, media_id)
            canonical_entries.append(
                {
                    "id": source.item_id,
                    "name": source.name,
                    "mime_type": source.mime_type,
                    "size": int(source.size),
                    "content_hash": source.content_hash,
                    "playable": bool(source.playable),
                }
            )
        encoded = json.dumps(canonical_entries, sort_keys=True, separators=(",", ":")).encode("utf-8")
        revision = hashlib.sha256(encoded).hexdigest()
        with self._library_lock:
            self._library_sources = prepared
            self._library_revision = revision
        return revision

    def request_offline_library_sync(self, receiver_ids: list[str]) -> None:
        """Ask already-running receiver apps to apply the current library revision."""
        now = time.time()
        with self._library_lock:
            for receiver_id in receiver_ids:
                request_id = uuid.uuid4().hex
                self._library_requests[receiver_id] = request_id
                self._library_status[receiver_id] = LibrarySyncStatus(
                    request_id=request_id,
                    state="pending",
                    revision=self._library_revision,
                    updated_at=now,
                )

    def receiver_library_manifest(self, receiver_id: str) -> dict[str, object]:
        with self._library_lock:
            entries = []
            for source, media_id in sorted(self._library_sources.values(), key=lambda item: item[0].item_id.casefold()):
                entries.append(
                    {
                        "id": source.item_id,
                        "name": source.name,
                        "mime_type": source.mime_type,
                        "size": int(source.size),
                        "content_hash": source.content_hash,
                        "playable": bool(source.playable),
                        "media_url": f"{self.base_url}/media/{media_id}/{quote(source.name)}",
                    }
                )
            return {
                "receiver_id": receiver_id,
                "revision": self._library_revision,
                "request_id": self._library_requests.get(receiver_id, ""),
                "entries": entries,
            }

    def record_receiver_library_status(
        self,
        receiver_id: str,
        *,
        request_id: str,
        state: str,
        revision: str,
        detail: str,
        stored_bytes: int,
    ) -> bool:
        with self._library_lock:
            expected_request = self._library_requests.get(receiver_id, "")
            if not expected_request or request_id != expected_request:
                return False
            self._library_status[receiver_id] = LibrarySyncStatus(
                request_id=request_id,
                state=state,
                revision=revision,
                detail=detail or "Receiver reported library status.",
                stored_bytes=max(0, int(stored_bytes)),
                updated_at=time.time(),
            )
            return True

    def receiver_library_status(self, receiver_id: str) -> LibrarySyncStatus | None:
        with self._library_lock:
            status = self._library_status.get(receiver_id)
            if status is None:
                return None
            return LibrarySyncStatus(
                request_id=status.request_id,
                state=status.state,
                revision=status.revision,
                detail=status.detail,
                stored_bytes=status.stored_bytes,
                updated_at=status.updated_at,
            )

    def library_item_for_path(self, path: Path) -> OfflineLibrarySource | None:
        try:
            normalized = path.resolve()
        except OSError:
            normalized = path
        with self._library_lock:
            for source, _ in self._library_sources.values():
                try:
                    candidate = source.path.resolve()
                except OSError:
                    candidate = source.path
                if candidate == normalized:
                    return source
        return None

    def receiver_url(
        self,
        receiver_id: str,
        preferred_alias: str | None = None,
        preferred_host: str | None = None,
    ) -> str:
        alias = self._receivers.alias_for(receiver_id, preferred_alias)
        self._receivers.ensure(receiver_id, preferred_alias, preferred_host)
        return f"{self.base_url}/r/{quote(alias)}"

    def receiver_state_alias_url(
        self,
        receiver_id: str,
        preferred_alias: str | None = None,
        preferred_host: str | None = None,
    ) -> str:
        alias = self._receivers.alias_for(receiver_id, preferred_alias)
        self._receivers.ensure(receiver_id, preferred_alias, preferred_host)
        return f"{self.base_url}/receiver-state-alias/{quote(alias)}"

    def update_receiver(
        self,
        receiver_id: str,
        *,
        source_name: str,
        mime_type: str,
        media_url: str | None,
        note: str,
        playback_state: str = "playing",
        start_position_seconds: float = 0.0,
        playback_token: int | None = None,
        stage_session_id: str | None = None,
        library_item_id: str | None = None,
        library_content_hash: str | None = None,
        preferred_alias: str | None = None,
        preferred_host: str | None = None,
    ) -> str:
        self._receivers.update(
            receiver_id,
            source_name=source_name,
            mime_type=mime_type,
            media_url=media_url,
            note=note,
            playback_state=playback_state,
            start_position_seconds=start_position_seconds,
            playback_token=playback_token,
            stage_session_id=stage_session_id,
            library_item_id=library_item_id,
            library_content_hash=library_content_hash,
            preferred_host=preferred_host,
        )
        return self.receiver_url(receiver_id, preferred_alias, preferred_host)

    def set_receiver_playback(
        self,
        receiver_id: str,
        *,
        playback_state: str,
        start_position_seconds: float | None = None,
        playback_token: int | None = None,
        preferred_alias: str | None = None,
        preferred_host: str | None = None,
    ) -> str:
        self._receivers.set_playback_state(
            receiver_id,
            playback_state=playback_state,
            start_position_seconds=start_position_seconds,
            playback_token=playback_token,
            preferred_host=preferred_host,
        )
        return self.receiver_url(receiver_id, preferred_alias, preferred_host)

    def wait_for_receiver_ready(self, receiver_id: str, *, after: float, timeout: float = 15.0) -> bool:
        """Wait until the receiver app fetches state after a launch request."""
        return self._receivers.wait_until_seen(receiver_id, after=after, timeout=timeout)


def _parse_range_header(range_header: str | None, file_size: int) -> tuple[int, int] | None:
    if not range_header:
        return None
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
    if match is None:
        return None

    start_text, end_text = match.groups()
    if not start_text and not end_text:
        return None

    if start_text:
        start = int(start_text)
        end = int(end_text) if end_text else file_size - 1
    else:
        suffix_length = int(end_text)
        if suffix_length <= 0:
            return None
        start = max(0, file_size - suffix_length)
        end = file_size - 1

    if start < 0 or end < start or start >= file_size:
        return None

    end = min(end, file_size - 1)
    return (start, end)


def _base36(value: int) -> str:
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    if value <= 0:
        return "0"
    result: list[str] = []
    current = value
    while current:
        current, remainder = divmod(current, 36)
        result.append(digits[remainder])
    return "".join(reversed(result))


def _receiver_html(receiver_id: str, receiver_alias: str, source_name: str) -> str:
    safe_receiver_id = html.escape(receiver_id)
    safe_receiver_alias = html.escape(receiver_alias)
    safe_source_name = html.escape(source_name)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>MultiHub Receiver</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #06111d;
      --panel: #0f2339;
      --line: rgba(124, 231, 255, 0.18);
      --text: #edf5ff;
      --muted: #9fb6ce;
      --accent: #7ce7ff;
      --accent2: #7effc6;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      background:
        radial-gradient(circle at top left, rgba(126, 255, 198, 0.14), transparent 32%),
        radial-gradient(circle at top right, rgba(124, 231, 255, 0.18), transparent 28%),
        linear-gradient(135deg, #020811 0%, var(--bg) 48%, #0a1a2d 100%);
      color: var(--text);
      font-family: "Segoe UI", sans-serif;
      display: grid;
      place-items: center;
      padding: 28px;
    }}
    .shell {{
      width: min(100%, 1200px);
      min-height: calc(100vh - 56px);
      border: 1px solid var(--line);
      border-radius: 28px;
      background: rgba(8, 24, 44, 0.86);
      overflow: hidden;
      box-shadow: 0 24px 60px rgba(0, 0, 0, 0.34);
      display: grid;
      grid-template-rows: auto 1fr auto;
    }}
    header {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      padding: 18px 22px;
      border-bottom: 1px solid var(--line);
      color: var(--muted);
      font-size: 14px;
    }}
    #viewport {{
      display: grid;
      place-items: center;
      padding: 20px;
      background: linear-gradient(140deg, rgba(6, 18, 31, 0.96), rgba(15, 38, 63, 0.98));
    }}
    img, video {{
      width: 100%;
      height: 100%;
      object-fit: contain;
      border-radius: 18px;
      background: black;
    }}
    audio {{
      width: min(90%, 520px);
    }}
    .card {{
      width: min(92%, 820px);
      padding: 34px;
      border: 1px solid var(--line);
      border-radius: 22px;
      background: linear-gradient(180deg, rgba(124, 231, 255, 0.1), rgba(5, 16, 28, 0.94));
      text-align: center;
    }}
    .card h1 {{
      margin: 0 0 12px;
      font-size: clamp(30px, 6vw, 58px);
    }}
    .card p {{
      margin: 0;
      color: #d4e0ec;
      line-height: 1.5;
      font-size: clamp(16px, 2.2vw, 22px);
    }}
    footer {{
      padding: 14px 22px 18px;
      color: var(--muted);
      font-size: 13px;
    }}
  </style>
</head>
<body>
  <div class="shell">
    <header>
      <div>MultiHub Receiver</div>
      <div id="headline">{safe_source_name}</div>
    </header>
    <main id="viewport">
      <div class="card">
        <h1>Receiver Ready</h1>
        <p>Waiting for the desktop app to send media to receiver <strong>{safe_receiver_id}</strong>.</p>
      </div>
    </main>
    <footer id="note">Pinned receiver: {safe_receiver_alias}. Leave this page open on the TV when direct playback is unavailable.</footer>
  </div>
  <script>
    const receiverId = {json.dumps(receiver_id)};
    const receiverAlias = {json.dumps(receiver_alias)};
    const viewport = document.getElementById("viewport");
    const headline = document.getElementById("headline");
    const note = document.getElementById("note");
    try {{
      localStorage.setItem("multihub.receiverAlias", receiverAlias);
    }} catch (error) {{}}

    function renderCard(title, body) {{
      viewport.innerHTML = "";
      const card = document.createElement("div");
      card.className = "card";
      const h1 = document.createElement("h1");
      h1.textContent = title;
      const p = document.createElement("p");
      p.textContent = body;
      card.append(h1, p);
      viewport.appendChild(card);
    }}

    function render(state) {{
      headline.textContent = state.source_name;
      note.textContent = state.note;
      if (!state.media_url) {{
        renderCard(state.source_name, state.note);
        return;
      }}

      viewport.innerHTML = "";
      if (state.mime_type.startsWith("image/")) {{
        const img = document.createElement("img");
        img.src = state.media_url;
        img.alt = state.source_name;
        viewport.appendChild(img);
        return;
      }}

      if (state.mime_type.startsWith("video/")) {{
        const video = document.createElement("video");
        video.src = state.media_url;
        video.controls = true;
        video.autoplay = true;
        video.loop = true;
        video.playsInline = true;
        viewport.appendChild(video);
        return;
      }}

      if (state.mime_type.startsWith("audio/")) {{
        const audio = document.createElement("audio");
        audio.src = state.media_url;
        audio.controls = true;
        audio.autoplay = true;
        viewport.appendChild(audio);
        return;
      }}

      renderCard(state.source_name, state.note);
    }}

    async function refresh() {{
      try {{
        const response = await fetch(`/receiver-state/${{receiverId}}`, {{ cache: "no-store" }});
        const state = await response.json();
        render(state);
      }} catch (error) {{
        renderCard("Receiver Offline", "The desktop app is not responding right now.");
      }}
    }}

    refresh();
    setInterval(refresh, 2000);
  </script>
</body>
</html>
"""


def _receiver_hub_html(entries: list[tuple[str, ReceiverState]]) -> str:
    aliases = [alias for alias, _ in entries]
    cards = []
    for alias, state in entries:
        safe_alias = html.escape(alias)
        safe_name = html.escape(state.source_name)
        cards.append(
            f"""
      <a class="receiver-card" href="/r/{safe_alias}" data-alias="{safe_alias}">
        <strong>TV {safe_alias}</strong>
        <span>{safe_name}</span>
      </a>
"""
        )
    cards_html = "".join(cards) or """
      <div class="receiver-card empty">
        <strong>No Receivers Yet</strong>
        <span>Send a source from the desktop app to create one.</span>
      </div>
"""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>MultiHub TV Hub</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #06111d;
      --panel: #0f2339;
      --line: rgba(124, 231, 255, 0.18);
      --text: #edf5ff;
      --muted: #9fb6ce;
      --accent: #7ce7ff;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      background:
        radial-gradient(circle at top left, rgba(126, 255, 198, 0.14), transparent 32%),
        radial-gradient(circle at top right, rgba(124, 231, 255, 0.18), transparent 28%),
        linear-gradient(135deg, #020811 0%, var(--bg) 48%, #0a1a2d 100%);
      color: var(--text);
      font-family: "Segoe UI", sans-serif;
      padding: 28px;
    }}
    .shell {{
      width: min(100%, 1080px);
      margin: 0 auto;
      border: 1px solid var(--line);
      border-radius: 28px;
      background: rgba(8, 24, 44, 0.86);
      overflow: hidden;
      box-shadow: 0 24px 60px rgba(0, 0, 0, 0.34);
    }}
    header {{
      padding: 22px;
      border-bottom: 1px solid var(--line);
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: clamp(32px, 5vw, 56px);
    }}
    p {{
      margin: 0;
      color: var(--muted);
      font-size: clamp(15px, 2vw, 20px);
      line-height: 1.5;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 16px;
      padding: 22px;
    }}
    .receiver-card {{
      display: grid;
      gap: 8px;
      padding: 22px;
      border: 1px solid var(--line);
      border-radius: 20px;
      background: linear-gradient(180deg, rgba(124, 231, 255, 0.08), rgba(5, 16, 28, 0.94));
      color: inherit;
      text-decoration: none;
      min-height: 140px;
      align-content: start;
    }}
    .receiver-card strong {{
      font-size: 28px;
      color: var(--accent);
    }}
    .receiver-card span {{
      color: #d4e0ec;
      line-height: 1.45;
    }}
    .receiver-card.empty {{
      pointer-events: none;
      opacity: 0.85;
    }}
    footer {{
      padding: 0 22px 22px;
      color: var(--muted);
      font-size: 14px;
    }}
  </style>
</head>
<body>
  <div class="shell">
    <header>
      <h1>MultiHub TV Hub</h1>
      <p>Open this page once on the TV. Pick the TV number below, then leave that receiver page open for future sends.</p>
    </header>
    <main class="grid">
{cards_html}
    </main>
    <footer>Tip: if this TV has already been paired before, it will reopen the last selected receiver page automatically.</footer>
  </div>
  <script>
    const knownAliases = new Set({json.dumps(aliases)});
    try {{
      const savedAlias = localStorage.getItem("multihub.receiverAlias");
      if (savedAlias && knownAliases.has(savedAlias)) {{
        window.location.replace(`/r/${{encodeURIComponent(savedAlias)}}`);
      }}
    }} catch (error) {{}}

    document.querySelectorAll(".receiver-card[data-alias]").forEach((card) => {{
      card.addEventListener("click", () => {{
        try {{
          localStorage.setItem("multihub.receiverAlias", card.dataset.alias || "");
        }} catch (error) {{}}
      }});
    }});
  </script>
</body>
</html>
"""
