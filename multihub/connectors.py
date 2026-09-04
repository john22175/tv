from __future__ import annotations

import base64
import concurrent.futures
import html
import importlib
import ipaddress
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from uuid import UUID
from urllib.parse import urljoin

import requests
from websockets.sync.client import connect as ws_connect

from .models import TVEndpoint

try:
    import pychromecast
    from pychromecast import discovery as chromecast_discovery
except ImportError:  # pragma: no cover - dependency is optional at import time
    pychromecast = None
    chromecast_discovery = None


AV_TRANSPORT_URN = "urn:schemas-upnp-org:service:AVTransport:1"
RENDERING_CONTROL_URN = "urn:schemas-upnp-org:service:RenderingControl:1"
CHROMECAST_QUEUE_LOAD = "QUEUE_LOAD"
CHROMECAST_REPEAT_SINGLE = "REPEAT_SINGLE"
SAMSUNG_BROWSER_APP_ID = "org.tizen.browser"
SAMSUNG_MULTIHUB_RECEIVER_APP_ID = "MHubRcvr01.MultiHubReceiver"
SAMSUNG_MULTIHUB_RECEIVER_PACKAGE_ID = "MHubRcvr01"
SAMSUNG_INSTALLED_APP_EVENT = "ed.installedApp.get"
SAMSUNG_APP_TYPE_NATIVE = "NATIVE_LAUNCH"
SAMSUNG_APP_TYPE_DEEP = "DEEP_LINK"
TIZEN_SDB_PORT = 26101
SAMSUNG_APPROVAL_WAIT_SECONDS = 30.0
SAMSUNG_APPROVAL_RETRY_ATTEMPTS = 2

# SDB uses one machine-wide server (port 26099).  Receiver launches can be
# requested in parallel for a stage, so serialize client invocations and
# recover a stale server before reporting a TV connection failure.
_SDB_CLI_LOCK = threading.RLock()

requests.packages.urllib3.disable_warnings()  # type: ignore[attr-defined]


def chromecast_support_status() -> tuple[bool, str]:
    try:
        importlib.import_module("pychromecast")
    except ImportError:
        executable = sys.executable or "python"
        return (
            False,
            "Chromecast support is unavailable in the Python interpreter running this app.\n\n"
            f"Interpreter: {executable}\n"
            "Install it there with:\n"
            f"\"{executable}\" -m pip install pychromecast",
        )
    return (True, "Chromecast support is available.")


def _require_pychromecast() -> tuple[object, object]:
    global pychromecast, chromecast_discovery
    if pychromecast is None or chromecast_discovery is None:
        try:
            pychromecast = importlib.import_module("pychromecast")
            chromecast_discovery = importlib.import_module("pychromecast.discovery")
        except ImportError as exc:
            executable = sys.executable or "python"
            raise RuntimeError(
                "Chromecast support requires pychromecast in the same Python environment that is running this app.\n\n"
                f"Interpreter: {executable}\n"
                "Install it with:\n"
                f"\"{executable}\" -m pip install pychromecast"
            ) from exc
    return pychromecast, chromecast_discovery


def _endpoint_id(
    *,
    host: str | None = None,
    chromecast_uuid: str | None = None,
    smartthings_device_id: str | None = None,
    fallback_prefix: str = "endpoint",
) -> str:
    if smartthings_device_id:
        return f"smartthings-{smartthings_device_id}"
    if host:
        return f"host-{host.replace('.', '-')}"
    if chromecast_uuid:
        return f"chromecast-{chromecast_uuid}"
    return f"{fallback_prefix}-{uuid.uuid4().hex}"


def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[-1]


def _find_first_text(parent: ET.Element, target: str) -> str | None:
    for element in parent.iter():
        if _strip_ns(element.tag) == target and element.text:
            return element.text.strip()
    return None


def _find_service_control_url(device: ET.Element, service_type: str, location_url: str) -> str | None:
    for service in device.iter():
        if _strip_ns(service.tag) != "service":
            continue
        current_service = _find_first_text(service, "serviceType")
        if current_service != service_type:
            continue
        control_url = _find_first_text(service, "controlURL")
        if control_url:
            return urljoin(location_url, control_url)
    return None


def detect_lan_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def detect_local_subnet() -> ipaddress.IPv4Network:
    return ipaddress.ip_network(f"{detect_lan_ip()}/24", strict=False)


def _host_has_open_port(host: str, port: int, timeout: float = 0.6) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def discover_cast_hosts(
    subnet: str | ipaddress.IPv4Network | None = None,
    *,
    port: int = 8009,
    timeout: float = 0.6,
    max_workers: int = 128,
) -> list[str]:
    if subnet is None:
        network = detect_local_subnet()
    elif isinstance(subnet, str):
        network = ipaddress.ip_network(subnet, strict=False)
    else:
        network = subnet

    found: list[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_host_has_open_port, str(ip), port, timeout): str(ip)
            for ip in network.hosts()
        }
        for future in concurrent.futures.as_completed(futures):
            host = futures[future]
            try:
                if future.result():
                    found.append(host)
            except OSError:
                continue

    found.sort()
    return found


def discover_dlna_tvs(timeout: float = 2.2) -> list[TVEndpoint]:
    message = "\r\n".join(
        [
            "M-SEARCH * HTTP/1.1",
            "HOST: 239.255.255.250:1900",
            'MAN: "ssdp:discover"',
            "MX: 2",
            "ST: urn:schemas-upnp-org:device:MediaRenderer:1",
            "",
            "",
        ]
    ).encode("utf-8")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.settimeout(timeout)
    sock.sendto(message, ("239.255.255.250", 1900))
    started_at = time.monotonic()
    locations: dict[str, dict[str, str]] = {}

    while time.monotonic() - started_at < timeout:
        try:
            data, address = sock.recvfrom(65535)
        except TimeoutError:
            break
        except OSError:
            break

        response = data.decode("utf-8", errors="ignore").split("\r\n")
        headers: dict[str, str] = {}
        for line in response[1:]:
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()

        location = headers.get("location")
        if location:
            headers["host"] = address[0]
            locations[location] = headers

    sock.close()

    endpoints: list[TVEndpoint] = []
    for location, headers in locations.items():
        try:
            response = requests.get(location, timeout=3)
            response.raise_for_status()
        except requests.RequestException:
            continue

        try:
            root = ET.fromstring(response.text)
        except ET.ParseError:
            continue

        device = None
        for element in root.iter():
            if _strip_ns(element.tag) == "device":
                device = element
                break
        if device is None:
            continue

        friendly_name = _find_first_text(device, "friendlyName") or headers.get("host", "Unknown TV")
        manufacturer = _find_first_text(device, "manufacturer") or "Unknown"
        model_name = _find_first_text(device, "modelName") or "Unknown"
        av_transport_url = _find_service_control_url(device, AV_TRANSPORT_URN, location)
        rendering_control_url = _find_service_control_url(device, RENDERING_CONTROL_URN, location)
        host = urllib.parse.urlparse(location).hostname or headers.get("host")
        source = "DLNA"

        endpoints.append(
            TVEndpoint(
                endpoint_id=_endpoint_id(host=host),
                name=friendly_name,
                host=host,
                manufacturer=manufacturer,
                model_name=model_name,
                av_transport_url=av_transport_url,
                rendering_control_url=rendering_control_url,
                location_url=location,
                samsung_remote_port=8002 if "samsung" in manufacturer.lower() else None,
                web_receiver_enabled=bool(host),
                source=source,
                metadata={"location_url": location},
            )
        )

    endpoints.sort(key=lambda endpoint: endpoint.name.lower())
    return endpoints


def discover_tvs(timeout: float = 2.2) -> list[TVEndpoint]:
    return discover_dlna_tvs(timeout=timeout)


def _build_metadata(title: str, mime_type: str, url: str) -> str:
    return (
        '<DIDL-Lite xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/" '
        'xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/">'
        '<item id="0" parentID="-1" restricted="1">'
        f"<dc:title>{html.escape(title)}</dc:title>"
        "<upnp:class>object.item.videoItem</upnp:class>"
        f'<res protocolInfo="http-get:*:{html.escape(mime_type)}:*">{html.escape(url)}</res>'
        "</item>"
        "</DIDL-Lite>"
    )


def _soap_request(control_url: str, service_urn: str, action: str, body: str) -> None:
    envelope = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        f"<s:Body><u:{action} xmlns:u=\"{service_urn}\">{body}</u:{action}></s:Body></s:Envelope>"
    )
    headers = {
        "Content-Type": 'text/xml; charset="utf-8"',
        "SOAPACTION": f'"{service_urn}#{action}"',
    }
    response = requests.post(control_url, data=envelope.encode("utf-8"), headers=headers, timeout=6)
    response.raise_for_status()


def play_to_renderer(endpoint: TVEndpoint, media_url: str, mime_type: str, title: str) -> None:
    if not endpoint.av_transport_url:
        raise RuntimeError("Selected TV does not expose an AVTransport endpoint.")

    metadata = _build_metadata(title=title, mime_type=mime_type, url=media_url)
    _soap_request(
        endpoint.av_transport_url,
        AV_TRANSPORT_URN,
        "SetAVTransportURI",
        (
            "<InstanceID>0</InstanceID>"
            f"<CurrentURI>{html.escape(media_url)}</CurrentURI>"
            f"<CurrentURIMetaData>{html.escape(metadata)}</CurrentURIMetaData>"
        ),
    )
    _soap_request(
        endpoint.av_transport_url,
        AV_TRANSPORT_URN,
        "Play",
        "<InstanceID>0</InstanceID><Speed>1</Speed>",
    )


def pause_renderer(endpoint: TVEndpoint) -> None:
    if not endpoint.av_transport_url:
        raise RuntimeError("Selected TV does not expose an AVTransport endpoint.")
    _soap_request(
        endpoint.av_transport_url,
        AV_TRANSPORT_URN,
        "Pause",
        "<InstanceID>0</InstanceID>",
    )


def stop_renderer(endpoint: TVEndpoint) -> None:
    if not endpoint.av_transport_url:
        raise RuntimeError("Selected TV does not expose an AVTransport endpoint.")
    _soap_request(
        endpoint.av_transport_url,
        AV_TRANSPORT_URN,
        "Stop",
        "<InstanceID>0</InstanceID>",
    )


def _samsung_payload_looks_like_tv(payload: dict) -> bool:
    device = payload.get("device")
    if not isinstance(device, dict):
        return False

    joined = " ".join(
        str(device.get(key, ""))
        for key in ("OS", "deviceType", "model", "modelName", "name", "manufacturer", "duid")
    ).lower()
    return "tizen" in joined or "samsung" in joined or payload.get("isSupport") is not None


def _extract_samsung_device_name(host: str, payload: dict) -> tuple[str, str | None]:
    device = payload.get("device")
    if not isinstance(device, dict):
        return (f"Samsung TV ({host})", None)

    name = (
        device.get("name")
        or device.get("friendlyName")
        or device.get("model")
        or device.get("modelName")
        or f"Samsung TV ({host})"
    )
    model_name = device.get("modelName") or device.get("model")
    return (html.unescape(str(name)), html.unescape(str(model_name)) if model_name else None)


def _extract_samsung_duid(payload: dict) -> str | None:
    device = payload.get("device")
    if not isinstance(device, dict):
        return None

    duid = device.get("duid")
    if duid is None:
        return None
    duid_text = html.unescape(str(duid)).strip()
    return duid_text or None


def probe_samsung_lan_tv(host: str, timeout: float = 2.0) -> TVEndpoint | None:
    candidates = [
        (8002, f"https://{host}:8002/api/v2/"),
        (8001, f"http://{host}:8001/api/v2/"),
    ]
    api_payload: dict | None = None
    available_ports: list[int] = []

    for port, url in candidates:
        try:
            response = requests.get(url, timeout=timeout, verify=False)
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError):
            continue

        if not isinstance(payload, dict) or not _samsung_payload_looks_like_tv(payload):
            continue

        available_ports.append(port)
        if api_payload is None:
            api_payload = payload

    if api_payload is None:
        return None

    preferred_port = 8002 if 8002 in available_ports else 8001
    name, model_name = _extract_samsung_device_name(host, api_payload)
    duid = _extract_samsung_duid(api_payload)
    return TVEndpoint(
        endpoint_id=_endpoint_id(host=host),
        name=name,
        host=host,
        manufacturer="Samsung",
        model_name=model_name,
        samsung_remote_port=preferred_port,
        web_receiver_enabled=True,
        source="Samsung LAN",
        metadata={
            "api_name": name,
            "api_model_name": model_name or "",
            "available_ports": ",".join(str(item) for item in available_ports),
            "duid": duid or "",
            "developer_ip": str(api_payload.get("device", {}).get("developerIP") or ""),
            "developer_mode": str(api_payload.get("device", {}).get("developerMode") or ""),
        },
    )


def discover_samsung_lan_tvs(
    subnet: str | ipaddress.IPv4Network | None = None,
    timeout: float = 2.0,
    max_workers: int = 64,
) -> list[TVEndpoint]:
    if subnet is None:
        network = detect_local_subnet()
    elif isinstance(subnet, str):
        network = ipaddress.ip_network(subnet, strict=False)
    else:
        network = subnet

    endpoints: dict[str, TVEndpoint] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(probe_samsung_lan_tv, str(ip), timeout): str(ip)
            for ip in network.hosts()
        }
        for future in concurrent.futures.as_completed(futures):
            endpoint = future.result()
            if endpoint is None:
                continue
            endpoints[endpoint.endpoint_id] = endpoint

    discovered = list(endpoints.values())
    discovered.sort(key=lambda endpoint: endpoint.name.lower())
    return discovered


def discover_chromecast_tvs(timeout: float = 6.0) -> list[TVEndpoint]:
    pychromecast_module, chromecast_discovery_module = _require_pychromecast()

    chromecasts, browser = pychromecast_module.get_chromecasts(timeout=timeout)
    try:
        endpoints: list[TVEndpoint] = []
        for cast in chromecasts:
            info = cast.cast_info
            name = info.friendly_name or info.host
            manufacturer = info.manufacturer or "Google"
            chromecast_uuid = str(info.uuid)
            endpoint = TVEndpoint(
                endpoint_id=_endpoint_id(chromecast_uuid=chromecast_uuid),
                name=name,
                host=info.host,
                manufacturer=manufacturer,
                model_name=info.model_name,
                chromecast_uuid=chromecast_uuid,
                chromecast_port=info.port,
                web_receiver_enabled=bool(info.host),
                source="Chromecast",
                metadata={"cast_type": info.cast_type or ""},
            )
            if info.host:
                samsung_endpoint = probe_samsung_lan_tv(info.host, timeout=1.2)
                if samsung_endpoint is not None:
                    endpoint.merge_from(samsung_endpoint)
            endpoints.append(endpoint)
        endpoints.sort(key=lambda endpoint: endpoint.name.lower())
        if endpoints:
            return endpoints
    finally:
        chromecast_discovery_module.stop_discovery(browser)

    known_hosts = discover_cast_hosts()
    if not known_hosts:
        return []

    endpoints_by_id: dict[str, TVEndpoint] = {}
    for host in known_hosts:
        chromecasts, browser = pychromecast_module.get_chromecasts(timeout=timeout, known_hosts=[host])
        try:
            for cast in chromecasts:
                info = cast.cast_info
                name = info.friendly_name or info.host
                manufacturer = info.manufacturer or "Google"
                chromecast_uuid = str(info.uuid)
                endpoint = TVEndpoint(
                    endpoint_id=_endpoint_id(host=info.host, chromecast_uuid=chromecast_uuid),
                    name=name,
                    host=info.host,
                    manufacturer=manufacturer,
                    model_name=info.model_name,
                    chromecast_uuid=chromecast_uuid,
                    chromecast_port=info.port,
                    web_receiver_enabled=bool(info.host),
                    source="Chromecast",
                    metadata={"cast_type": info.cast_type or "", "known_host_fallback": "true"},
                )
                if info.host:
                    samsung_endpoint = probe_samsung_lan_tv(info.host, timeout=1.2)
                    if samsung_endpoint is not None:
                        endpoint.merge_from(samsung_endpoint)
                endpoints_by_id[endpoint.endpoint_id] = endpoint
        finally:
            chromecast_discovery_module.stop_discovery(browser)
            for cast in chromecasts:
                try:
                    cast.disconnect(timeout=2)
                except Exception:
                    continue

    endpoints = list(endpoints_by_id.values())
    endpoints.sort(key=lambda endpoint: endpoint.name.lower())
    return endpoints


def play_to_chromecast(
    endpoint: TVEndpoint,
    media_url: str,
    mime_type: str,
    title: str,
    *,
    start_time_seconds: float = 0.0,
) -> None:
    pychromecast_module, chromecast_discovery_module = _require_pychromecast()
    if not endpoint.chromecast_uuid:
        raise RuntimeError("Selected endpoint does not have Chromecast metadata.")

    known_hosts = [endpoint.host] if endpoint.host else None
    chromecasts, browser = pychromecast_module.get_listed_chromecasts(
        uuids=[UUID(endpoint.chromecast_uuid)],
        known_hosts=known_hosts,
        timeout=8,
        discovery_timeout=4,
    )
    fallback_browser = None
    try:
        if not chromecasts and known_hosts:
            fallback_casts, fallback_browser = pychromecast_module.get_chromecasts(
                known_hosts=known_hosts,
                timeout=8,
            )
            target_uuid = str(endpoint.chromecast_uuid)
            target_host = endpoint.host
            chromecasts = [
                cast
                for cast in fallback_casts
                if (
                    str(getattr(getattr(cast, "cast_info", None), "uuid", "")) == target_uuid
                    or getattr(getattr(cast, "cast_info", None), "host", None) == target_host
                )
            ]

        if not chromecasts:
            raise RuntimeError(f"Chromecast {endpoint.name} could not be resolved on the network.")

        cast = chromecasts[0]
        cast.wait(timeout=10)
        media_controller = cast.media_controller
        if mime_type.startswith("video/"):
            media_controller.send_message(
                _build_chromecast_looping_video_request(media_url, mime_type, title, start_time_seconds=start_time_seconds),
                inc_session_id=True,
            )
        else:
            media_controller.play_media(
                media_url,
                mime_type,
                title=title,
                autoplay=True,
                current_time=max(0.0, float(start_time_seconds)),
                stream_type="BUFFERED",
                metadata={"title": title},
            )
        media_controller.block_until_active(timeout=10)

        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            time.sleep(1)
            media_controller.update_status()
            status = media_controller.status
            if status is None:
                continue
            if status.media_session_id is not None:
                return
            if status.content_id and status.player_state in {"PLAYING", "BUFFERING", "PAUSED", "IDLE"}:
                return

        raise RuntimeError(
            "Chromecast command was sent, but the device never started a media session. "
            "This TV may not support generic Cast playback for local media, or it may not be able to reach this computer."
        )
    finally:
        chromecast_discovery_module.stop_discovery(browser)
        if fallback_browser is not None:
            chromecast_discovery_module.stop_discovery(fallback_browser)
        for cast in chromecasts:
            try:
                cast.disconnect(timeout=2)
            except Exception:
                continue


def _build_chromecast_looping_video_request(
    media_url: str,
    mime_type: str,
    title: str,
    *,
    start_time_seconds: float = 0.0,
) -> dict[str, Any]:
    start_time = max(0.0, float(start_time_seconds))
    media: dict[str, Any] = {
        "contentId": media_url,
        "streamType": "BUFFERED",
        "contentType": mime_type,
        "metadata": {
            "title": title,
            "metadataType": 0,
        },
    }
    return {
        "type": CHROMECAST_QUEUE_LOAD,
        "items": [
            {
                "media": media,
                "autoplay": True,
                "startTime": start_time,
                "preloadTime": 0,
            }
        ],
        "startIndex": 0,
        "currentTime": start_time,
        "repeatMode": CHROMECAST_REPEAT_SINGLE,
        "autoplay": True,
        "customData": {},
    }


def pause_chromecast(endpoint: TVEndpoint) -> None:
    pychromecast_module, chromecast_discovery_module = _require_pychromecast()
    if not endpoint.chromecast_uuid:
        raise RuntimeError("Selected endpoint does not have Chromecast metadata.")

    known_hosts = [endpoint.host] if endpoint.host else None
    chromecasts, browser = pychromecast_module.get_listed_chromecasts(
        uuids=[UUID(endpoint.chromecast_uuid)],
        known_hosts=known_hosts,
        timeout=8,
        discovery_timeout=4,
    )
    try:
        if not chromecasts:
            raise RuntimeError(f"Chromecast {endpoint.name} could not be resolved on the network.")
        cast = chromecasts[0]
        cast.wait(timeout=10)
        cast.media_controller.block_until_active(timeout=10)
        cast.media_controller.pause()
    finally:
        chromecast_discovery_module.stop_discovery(browser)
        for cast in chromecasts:
            try:
                cast.disconnect(timeout=2)
            except Exception:
                continue


@dataclass(slots=True)
class SamsungRemoteResult:
    token: str | None = None
    response: dict | None = None


@dataclass(slots=True)
class SamsungRemoteProbe:
    ok: bool
    port: int
    state: str
    detail: str
    token: str | None = None
    response: dict | None = None
    control_name: str = "samsung_remote"


def _resolve_tizen_cli(tool_name: str) -> str:
    executable = f"{tool_name}.exe" if os.name == "nt" else tool_name
    resolved = shutil.which(executable) or shutil.which(tool_name)
    if resolved:
        return resolved

    home = Path.home()
    candidates = {
        "sdb": [
            Path("C:/tizen-studio/tools/sdb.exe"),
            home / ".tizen-extension-platform/server/sdktools/data/tools/sdb.exe",
        ],
        "tz": [
            Path("C:/tizen-studio/tools/tizen-core/tz.exe"),
            home / ".tizen-extension-platform/server/sdktools/data/tools/tizen-core/tz.exe",
        ],
    }
    for candidate in candidates.get(tool_name, []):
        if candidate.exists():
            return str(candidate)

    raise RuntimeError(
        f"Could not locate the Tizen {tool_name} CLI. Install Tizen Studio or add {tool_name} to PATH."
    )


def _tizen_device_serial(endpoint: TVEndpoint) -> str:
    if not endpoint.host:
        raise RuntimeError("Tizen launch requires a TV host/IP.")
    return f"{endpoint.host}:{TIZEN_SDB_PORT}"


def _run_tizen_cli(args: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
    def run() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            encoding="utf-8",
            errors="replace",
        )

    is_sdb = Path(args[0]).stem.lower() == "sdb"
    if not is_sdb:
        return run()

    with _SDB_CLI_LOCK:
        completed = run()
        output = "\n".join((completed.stdout or "", completed.stderr or "")).lower()
        if "server is not running" not in output:
            return completed

        # A listener can remain after the SDB server itself has stopped
        # responding.  Restarting through the CLI replaces that stale process.
        subprocess.run(
            [args[0], "start-server"],
            capture_output=True,
            text=True,
            timeout=min(timeout, 15),
            check=False,
            encoding="utf-8",
            errors="replace",
        )
        return run()


def _format_tizen_cli_failure(result: subprocess.CompletedProcess[str]) -> str:
    parts: list[str] = []
    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()
    if stdout:
        parts.append(stdout)
    if stderr:
        parts.append(stderr)
    if not parts:
        parts.append(f"Exit code: {result.returncode}")
    return "\n".join(parts)


def _normalize_tizen_device_id(value: str) -> str:
    normalized = value.strip().lower()
    if normalized.startswith("uuid:"):
        normalized = normalized[5:]
    return normalized


def _extract_tizen_connected_serial(output: str) -> str | None:
    for line in output.splitlines():
        match = re.search(r"(?:already\s+)?connected to\s+([^\s]+)", line, flags=re.IGNORECASE)
        if match:
            serial = match.group(1).strip()
            if serial:
                return serial
    return None


def _parse_tizen_devices_output(output: str) -> list[tuple[str, str]]:
    devices: list[tuple[str, str]] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("*") or line.lower().startswith("list of devices"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        devices.append((parts[0].strip(), parts[1].strip()))
    return devices


def _score_tizen_device_serial(
    serial: str,
    endpoint: TVEndpoint,
    *,
    connected_serial: str | None,
) -> int:
    score = 0
    normalized_serial = _normalize_tizen_device_id(serial)
    host = (endpoint.host or "").strip().lower()
    direct_serial = f"{host}:{TIZEN_SDB_PORT}" if host else ""
    normalized_connected = _normalize_tizen_device_id(connected_serial or "")
    normalized_duid = _normalize_tizen_device_id(endpoint.metadata.get("duid", ""))

    if normalized_connected and normalized_serial == normalized_connected:
        score += 100
    if direct_serial and serial.lower() == direct_serial:
        score += 90
    if host and serial.lower() == host:
        score += 85
    if host and serial.lower().startswith(f"{host}:"):
        score += 80
    if normalized_duid and normalized_serial == normalized_duid:
        score += 70
    if normalized_duid and normalized_duid in normalized_serial:
        score += 40
    return score


def _resolve_tizen_target_serial(
    sdb_path: str,
    endpoint: TVEndpoint,
    *,
    connected_serial: str | None,
) -> str:
    fallback_candidates: list[str] = []
    for candidate in (
        connected_serial,
        _tizen_device_serial(endpoint),
        endpoint.host or "",
        endpoint.metadata.get("duid", ""),
        _normalize_tizen_device_id(endpoint.metadata.get("duid", "")),
    ):
        value = str(candidate or "").strip()
        if value and value not in fallback_candidates:
            fallback_candidates.append(value)

    listed = _run_tizen_cli([sdb_path, "devices"], timeout=20)
    if listed.returncode == 0:
        devices = _parse_tizen_devices_output(listed.stdout or "")
        if devices:
            ranked = sorted(
                devices,
                key=lambda item: _score_tizen_device_serial(item[0], endpoint, connected_serial=connected_serial),
                reverse=True,
            )
            best_serial, _ = ranked[0]
            if _score_tizen_device_serial(best_serial, endpoint, connected_serial=connected_serial) > 0:
                return best_serial

            online_devices = [serial for serial, state in devices if state.lower() == "device"]
            if len(online_devices) == 1:
                return online_devices[0]

    return fallback_candidates[0]


def _describe_tizen_connect_error(endpoint: TVEndpoint, detail: str) -> str:
    lowered = detail.lower()
    if "failed to connect to" not in lowered and "server is not running" not in lowered:
        return detail

    target = endpoint.display_name if hasattr(endpoint, "display_name") else (endpoint.host or "the TV")
    return (
        f"SDB could not reach {target} on port {TIZEN_SDB_PORT}. "
        "This usually means Tizen Developer Mode is off on the TV, this computer's IP is not registered "
        "in the TV's Developer Mode settings, or the TV needs a reboot after Developer Mode was enabled.\n\n"
        f"{detail}"
    )


def _describe_tizen_target_error(endpoint: TVEndpoint, serial: str, detail: str) -> str:
    lowered = detail.lower()
    if "serial number" not in lowered and "target not found" not in lowered:
        return detail

    target = endpoint.display_name if hasattr(endpoint, "display_name") else (endpoint.host or "the TV")
    return (
        f"Tizen connected, but SDB could not resolve the active device identifier for {target}. "
        f"MultiHub tried the target serial '{serial}'. Refresh the TV info/DUID and reconnect the TV in Tizen Device Manager if needed.\n\n"
        f"{detail}"
    )


def _connect_tizen_device(sdb_path: str, endpoint: TVEndpoint) -> tuple[str | None, str | None]:
    if not endpoint.host:
        return (None, "Tizen launch requires a TV host/IP.")

    connected = _run_tizen_cli(
        [sdb_path, "connect", endpoint.host],
        timeout=20,
    )
    if connected.returncode == 0:
        output = "\n".join(part for part in (connected.stdout or "", connected.stderr or "") if part)
        return (_extract_tizen_connected_serial(output), None)
    return (None, _format_tizen_cli_failure(connected))


def open_tizen_receiver_app(endpoint: TVEndpoint) -> SamsungRemoteProbe:
    remote_probe: SamsungRemoteProbe | None = None
    if endpoint.can_use_samsung_remote:
        # Opening the receiver performs the authorization handshake and sends the
        # launch command on the same socket.  Probing first created a second
        # connection for every source drop, which can make Samsung TVs show a
        # fresh approval prompt and leave the launch command behind it.
        remote_probe = open_samsung_receiver_app(endpoint)
        if remote_probe.ok:
            endpoint.samsung_remote_port = remote_probe.port
            if remote_probe.token:
                endpoint.samsung_remote_token = remote_probe.token
            return remote_probe

    if not endpoint.host:
        return SamsungRemoteProbe(
            ok=False,
            port=TIZEN_SDB_PORT,
            state="missing_host",
            detail="Tizen launch requires a TV host/IP.",
            control_name="tizen_app_launch",
        )

    sdb_path = _resolve_tizen_cli("sdb")
    tz_path = _resolve_tizen_cli("tz")

    connected_serial, connect_error = _connect_tizen_device(sdb_path, endpoint)
    if connect_error is not None:
        detail = _describe_tizen_connect_error(endpoint, connect_error)
        if endpoint.can_use_samsung_remote:
            remote_context: list[str] = []
            if remote_probe is not None:
                remote_context.append(f"Samsung LAN launch: {remote_probe.state}")
                remote_context.append(remote_probe.detail)
            detail = (
                "Samsung LAN path failed before SDB fallback.\n"
                + "\n".join(remote_context)
                + "\n\n"
                f"{detail}"
            )
        return SamsungRemoteProbe(
            ok=False,
            port=TIZEN_SDB_PORT,
            state="device_error",
            detail=detail,
            control_name="tizen_app_launch",
        )

    serial = _resolve_tizen_target_serial(sdb_path, endpoint, connected_serial=connected_serial)

    installed = _run_tizen_cli(
        [sdb_path, "-s", serial, "shell", "0", "applist"],
        timeout=20,
    )
    if installed.returncode != 0:
        return SamsungRemoteProbe(
            ok=False,
            port=TIZEN_SDB_PORT,
            state="device_error",
            detail=_describe_tizen_target_error(endpoint, serial, _format_tizen_cli_failure(installed)),
            control_name="tizen_app_launch",
        )

    if SAMSUNG_MULTIHUB_RECEIVER_APP_ID not in (installed.stdout or ""):
        return SamsungRemoteProbe(
            ok=False,
            port=TIZEN_SDB_PORT,
            state="not_installed",
            detail=(
                "MultiHub Receiver app is not installed on this TV. "
                "Install the signed Tizen package first."
            ),
            control_name="tizen_app_launch",
        )

    launched = _run_tizen_cli(
        [tz_path, "run", "-p", SAMSUNG_MULTIHUB_RECEIVER_PACKAGE_ID, "-e", serial],
        timeout=30,
    )
    if launched.returncode != 0:
        return SamsungRemoteProbe(
            ok=False,
            port=TIZEN_SDB_PORT,
            state="launch_failed",
            detail=_describe_tizen_target_error(endpoint, serial, _format_tizen_cli_failure(launched)),
            control_name="tizen_app_launch",
        )

    launch_output = _format_tizen_cli_failure(launched)
    return SamsungRemoteProbe(
        ok=True,
        port=TIZEN_SDB_PORT,
        state="launched",
        detail=launch_output or "MultiHub Receiver app launch command was sent over the Tizen dev channel.",
        control_name="tizen_app_launch",
    )


def _candidate_samsung_remote_ports(endpoint: TVEndpoint) -> list[int]:
    ports: list[int] = []

    def add(port: int | None) -> None:
        if isinstance(port, int) and port > 0 and port not in ports:
            ports.append(port)

    add(endpoint.samsung_remote_port)
    raw_ports = endpoint.metadata.get("available_ports", "")
    for raw_port in str(raw_ports).split(","):
        value = raw_port.strip()
        if not value:
            continue
        try:
            add(int(value))
        except ValueError:
            continue

    add(8002)
    add(8001)
    return ports


def _candidate_samsung_api_ports(endpoint: TVEndpoint) -> list[int]:
    return _candidate_samsung_remote_ports(endpoint)


def _samsung_remote_key_payload(key: str) -> dict[str, Any]:
    return {
        "method": "ms.remote.control",
        "params": {
            "Cmd": "Click",
            "DataOfCmd": key,
            "Option": "false",
            "TypeOfRemote": "SendRemoteKey",
        },
    }


def _samsung_send_text_sequence(
    socket_conn: Any,
    text: str,
    *,
    finalize: bool = True,
    inter_message_delay: float = 0.0,
) -> None:
    if not text:
        return

    socket_conn.send(
        json.dumps(
            {
                "method": "ms.channel.emit",
                "params": {
                    "event": "custom.remote.textReceived",
                    "to": "broadcast",
                },
            }
        )
    )
    if inter_message_delay > 0:
        time.sleep(inter_message_delay)
    socket_conn.send(
        json.dumps(
            {
                "method": "ms.remote.control",
                "params": {
                    "Cmd": base64.b64encode(text.encode("utf-8")).decode("ascii"),
                    "DataOfCmd": "base64",
                    "TypeOfRemote": "SendInputString",
                },
            }
        )
    )
    if not finalize:
        return
    if inter_message_delay > 0:
        time.sleep(inter_message_delay)
    socket_conn.send(json.dumps({"method": "ms.remote.control", "params": {"TypeOfRemote": "SendInputEnd"}}))


def _samsung_launch_app(socket_conn: Any, app_id: str, action_type: str, meta_tag: str = "") -> None:
    socket_conn.send(
        json.dumps(
            {
                "method": "ms.channel.emit",
                "params": {
                    "event": "ed.apps.launch",
                    "to": "host",
                    "data": {
                        "appId": app_id,
                        "action_type": action_type,
                        "metaTag": meta_tag,
                    },
                },
            }
        )
    )


def _samsung_launch_type_for_app(app_type: Any) -> str:
    value = str(app_type).strip().upper()
    if value == "2":
        return SAMSUNG_APP_TYPE_DEEP
    if value == "4":
        return SAMSUNG_APP_TYPE_NATIVE
    if value in {SAMSUNG_APP_TYPE_DEEP, SAMSUNG_APP_TYPE_NATIVE}:
        return value
    return SAMSUNG_APP_TYPE_NATIVE


def _samsung_query_installed_apps(socket_conn: Any, timeout_seconds: float = 2.5) -> list[dict[str, Any]]:
    socket_conn.send(
        json.dumps(
            {
                "method": "ms.channel.emit",
                "params": {
                    "event": SAMSUNG_INSTALLED_APP_EVENT,
                    "to": "host",
                },
            }
        )
    )

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        remaining = max(0.1, deadline - time.monotonic())
        try:
            message = socket_conn.recv(timeout=remaining)
        except TimeoutError:
            break
        except Exception:
            break

        if not isinstance(message, str):
            continue

        try:
            payload = json.loads(message)
        except ValueError:
            continue

        if payload.get("event") != SAMSUNG_INSTALLED_APP_EVENT:
            continue

        data = payload.get("data", {}).get("data")
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        break

    return []


def _samsung_resolve_browser_app(endpoint: TVEndpoint, socket_conn: Any) -> tuple[str, str] | None:
    cached_app_id = endpoint.metadata.get("browser_app_id", "").strip()
    cached_launch_type = endpoint.metadata.get("browser_launch_type", "").strip()
    if cached_app_id and cached_launch_type:
        return (cached_app_id, cached_launch_type)

    apps = _samsung_query_installed_apps(socket_conn)
    if not apps:
        return None

    def score(app: dict[str, Any]) -> tuple[int, int]:
        app_id = str(app.get("appId") or "")
        name = str(app.get("name") or "").lower()
        score_value = 0
        if app_id == SAMSUNG_BROWSER_APP_ID:
            score_value += 100
        if "browser" in app_id.lower():
            score_value += 40
        if "internet" in name:
            score_value += 30
        if "browser" in name:
            score_value += 20
        return (score_value, len(app_id))

    candidates = [app for app in apps if score(app)[0] > 0]
    if not candidates:
        return None

    best = max(candidates, key=score)
    app_id = str(best.get("appId") or "").strip()
    if not app_id:
        return None

    launch_type = _samsung_launch_type_for_app(best.get("app_type"))
    endpoint.metadata["browser_app_id"] = app_id
    endpoint.metadata["browser_launch_type"] = launch_type
    if best.get("name"):
        endpoint.metadata["browser_app_name"] = str(best.get("name"))
    return (app_id, launch_type)


def _samsung_resolve_receiver_app(endpoint: TVEndpoint, socket_conn: Any) -> tuple[str, str] | None:
    cached_app_id = endpoint.metadata.get("receiver_app_id", "").strip()
    cached_launch_type = endpoint.metadata.get("receiver_launch_type", "").strip()
    if cached_app_id and cached_launch_type:
        return (cached_app_id, cached_launch_type)

    apps = _samsung_query_installed_apps(socket_conn)
    if not apps:
        return None

    def score(app: dict[str, Any]) -> tuple[int, int]:
        app_id = str(app.get("appId") or "")
        name = str(app.get("name") or "").lower()
        score_value = 0
        if app_id == SAMSUNG_MULTIHUB_RECEIVER_APP_ID:
            score_value += 100
        if "multihub" in name:
            score_value += 30
        if "receiver" in name:
            score_value += 20
        return (score_value, len(app_id))

    candidates = [app for app in apps if score(app)[0] > 0]
    if not candidates:
        return None

    best = max(candidates, key=score)
    app_id = str(best.get("appId") or "").strip()
    if not app_id:
        return None

    launch_type = _samsung_launch_type_for_app(best.get("app_type"))
    endpoint.metadata["receiver_app_id"] = app_id
    endpoint.metadata["receiver_launch_type"] = launch_type
    if best.get("name"):
        endpoint.metadata["receiver_app_name"] = str(best.get("name"))
    return (app_id, launch_type)


def _try_rest_launch_samsung_app(endpoint: TVEndpoint, app_id: str) -> None:
    if not endpoint.host:
        return

    for port in _candidate_samsung_api_ports(endpoint):
        scheme = "https" if port == 8002 else "http"
        url = f"{scheme}://{endpoint.host}:{port}/api/v2/applications/{urllib.parse.quote(app_id)}"
        try:
            response = requests.post(url, timeout=3, verify=False)
            if response.ok or response.status_code in {200, 201, 202, 204}:
                return
        except requests.RequestException:
            continue


def _samsung_remote_connection(
    endpoint: TVEndpoint,
    *,
    client_name: str,
    success_detail: str,
    action: Callable[[Any], None] | None = None,
) -> SamsungRemoteProbe:
    probe_timeout = 25
    if not endpoint.host:
        return SamsungRemoteProbe(
            ok=False,
            port=endpoint.samsung_remote_port or 8002,
            state="missing_host",
            detail="Samsung remote control requires a TV host/IP.",
        )

    encoded_name = base64.b64encode(client_name.encode("utf-8")).decode("ascii")

    candidate_ports = _candidate_samsung_remote_ports(endpoint)
    last_probe: SamsungRemoteProbe | None = None

    for port in candidate_ports:
        attempt_tokens: list[str | None] = []
        saved_token = (endpoint.samsung_remote_token or "").strip() or None
        if saved_token:
            attempt_tokens.append(saved_token)
        # Keep the same pairing session open while the person approves it. A
        # single final reconnect covers TVs that close the socket immediately;
        # repeatedly reconnecting creates a new approval prompt every time.
        attempt_tokens.extend([None] * SAMSUNG_APPROVAL_RETRY_ATTEMPTS)

        for attempt_index, requested_token in enumerate(attempt_tokens):
            scheme = "wss" if port == 8002 else "ws"
            params = {"name": encoded_name}
            if requested_token:
                params["token"] = requested_token
            query = urllib.parse.urlencode(params)
            uri = f"{scheme}://{endpoint.host}:{port}/api/v2/channels/samsung.remote.control?{query}"
            ssl_context = ssl._create_unverified_context() if scheme == "wss" else None

            try:
                with ws_connect(uri, ssl_context=ssl_context, open_timeout=probe_timeout, close_timeout=2) as socket_conn:
                    try:
                        initial = socket_conn.recv(timeout=probe_timeout)
                    except TimeoutError:
                        last_probe = SamsungRemoteProbe(
                            ok=False,
                            port=port,
                            state="timeout",
                            detail="The TV did not complete the Samsung remote handshake. An approval prompt may not have appeared.",
                        )
                        continue

                    initial_payload = json.loads(initial) if isinstance(initial, str) else None
                    token = None
                    if initial_payload:
                        token = (
                            initial_payload.get("data", {}).get("token")
                            if isinstance(initial_payload.get("data"), dict)
                            else None
                        )
                        event = initial_payload.get("event")
                        if event == "ms.channel.unauthorized":
                            if requested_token and attempt_index == 0:
                                endpoint.samsung_remote_token = None
                                last_probe = SamsungRemoteProbe(
                                    ok=False,
                                    port=port,
                                    state="unauthorized",
                                    detail="The saved Samsung LAN token was rejected. Retrying once without a token so the TV can request approval again.",
                                    token=token,
                                    response=initial_payload,
                                )
                                continue
                            if requested_token is None:
                                approval_deadline = time.monotonic() + SAMSUNG_APPROVAL_WAIT_SECONDS
                                while time.monotonic() < approval_deadline:
                                    try:
                                        approval_message = socket_conn.recv(
                                            timeout=max(0.1, approval_deadline - time.monotonic())
                                        )
                                    except (TimeoutError, Exception):
                                        break
                                    if not isinstance(approval_message, str):
                                        continue
                                    try:
                                        approval_payload = json.loads(approval_message)
                                    except ValueError:
                                        continue
                                    approval_event = approval_payload.get("event")
                                    if approval_event == "ms.channel.connect":
                                        initial_payload = approval_payload
                                        token = (
                                            approval_payload.get("data", {}).get("token")
                                            if isinstance(approval_payload.get("data"), dict)
                                            else None
                                        )
                                        event = approval_event
                                        break
                                    if approval_event == "ms.channel.unauthorized":
                                        break

                                if event == "ms.channel.connect":
                                    # The approval arrived on this session: send the
                                    # app-launch command without reconnecting.
                                    pass
                                elif attempt_index < len(attempt_tokens) - 1:
                                    last_probe = SamsungRemoteProbe(
                                        ok=False,
                                        port=port,
                                        state="unauthorized",
                                        detail=(
                                            "Waiting for Samsung LAN approval on the TV and retrying the receiver launch. "
                                            f"This can take up to {SAMSUNG_APPROVAL_WAIT_SECONDS:.0f} seconds."
                                        ),
                                        token=token,
                                        response=initial_payload,
                                    )
                                    continue
                                else:
                                    return SamsungRemoteProbe(
                                        ok=False,
                                        port=port,
                                        state="unauthorized",
                                        detail=(
                                            "The TV did not authorize Samsung LAN control within the approval window. "
                                            "Confirm the pairing prompt on the TV, then retry."
                                        ),
                                        token=token,
                                        response=initial_payload,
                                    )
                        if event == "ms.channel.timeOut":
                            last_probe = SamsungRemoteProbe(
                                ok=False,
                                port=port,
                                state="timeout",
                                detail="The Samsung LAN remote session timed out before the TV authorized it.",
                                token=token,
                                response=initial_payload,
                            )
                            continue

                    if action is not None:
                        action(socket_conn)

                    endpoint.samsung_remote_port = port
                    return SamsungRemoteProbe(
                        ok=True,
                        port=port,
                        state="authorized",
                        detail=success_detail,
                        token=token,
                        response=initial_payload,
                    )
            except Exception as exc:
                last_probe = SamsungRemoteProbe(
                    ok=False,
                    port=port,
                    state="connection_error",
                    detail=f"{type(exc).__name__}: {exc}",
                )

    if last_probe is None:
        return SamsungRemoteProbe(
            ok=False,
            port=endpoint.samsung_remote_port or 8002,
            state="connection_error",
            detail="No Samsung remote ports were available for probing.",
        )

    if len(candidate_ports) > 1:
        last_probe.detail = f"{last_probe.detail} Tried ports: {', '.join(str(port) for port in candidate_ports)}."
    return last_probe


def probe_samsung_remote_access(
    endpoint: TVEndpoint,
    *,
    key: str | None = None,
    client_name: str = "PyQt MultiHub",
) -> SamsungRemoteProbe:
    def action(socket_conn: Any) -> None:
        if not key:
            return
        socket_conn.send(json.dumps(_samsung_remote_key_payload(key)))

    return _samsung_remote_connection(
        endpoint,
        client_name=client_name,
        success_detail="Samsung LAN remote session is authorized.",
        action=action,
    )


def open_samsung_browser_url(
    endpoint: TVEndpoint,
    url: str,
    *,
    client_name: str = "PyQt MultiHub",
) -> SamsungRemoteProbe:
    def action(socket_conn: Any) -> None:
        launch_targets: list[tuple[str, str]] = []
        resolved_browser = _samsung_resolve_browser_app(endpoint, socket_conn)
        if resolved_browser is not None:
            launch_targets.append(resolved_browser)
        launch_targets.append((SAMSUNG_BROWSER_APP_ID, SAMSUNG_APP_TYPE_NATIVE))
        launch_targets.append((SAMSUNG_BROWSER_APP_ID, SAMSUNG_APP_TYPE_DEEP))

        seen: set[tuple[str, str]] = set()
        unique_targets: list[tuple[str, str]] = []
        for target in launch_targets:
            if target in seen:
                continue
            seen.add(target)
            unique_targets.append(target)

        for index, (app_id, launch_type) in enumerate(unique_targets):
            _try_rest_launch_samsung_app(endpoint, app_id)
            _samsung_launch_app(socket_conn, app_id, launch_type, url)
            if index < len(unique_targets) - 1:
                time.sleep(0.5)
        time.sleep(0.75)

    return _samsung_remote_connection(
        endpoint,
        client_name=client_name,
        success_detail="Samsung browser launch command was sent.",
        action=action,
    )


def open_samsung_receiver_app(
    endpoint: TVEndpoint,
    *,
    client_name: str = "PyQt MultiHub",
) -> SamsungRemoteProbe:
    def action(socket_conn: Any) -> None:
        resolved_receiver = _samsung_resolve_receiver_app(endpoint, socket_conn)
        if resolved_receiver is None:
            raise RuntimeError(
                "MultiHub Receiver app is not installed on this TV. "
                "Install the signed Tizen package first."
            )

        app_id, launch_type = resolved_receiver
        _try_rest_launch_samsung_app(endpoint, app_id)
        _samsung_launch_app(socket_conn, app_id, launch_type)
        if launch_type != SAMSUNG_APP_TYPE_NATIVE:
            time.sleep(0.35)
            _samsung_launch_app(socket_conn, app_id, SAMSUNG_APP_TYPE_NATIVE)
        time.sleep(0.75)

    return _samsung_remote_connection(
        endpoint,
        client_name=client_name,
        success_detail="MultiHub Receiver app launch command was sent.",
        action=action,
    )


def open_samsung_receiver_target(
    endpoint: TVEndpoint,
    url: str,
    *,
    client_name: str = "PyQt MultiHub",
) -> SamsungRemoteProbe:
    del url
    return open_samsung_receiver_app(endpoint, client_name=client_name)


def send_samsung_remote_key(
    endpoint: TVEndpoint,
    key: str,
    client_name: str = "PyQt MultiHub",
) -> SamsungRemoteResult:
    probe = probe_samsung_remote_access(endpoint, key=key, client_name=client_name)
    if probe.ok:
        return SamsungRemoteResult(token=probe.token, response=probe.response)

    if probe.state == "unauthorized":
        raise RuntimeError(
            "Samsung LAN remote was rejected by the TV. Open the TV settings and allow the device connection, then retry."
        )
    if probe.state == "timeout":
        raise RuntimeError(
            "Samsung LAN remote timed out before authorization completed. If the TV shows an approval prompt, allow it and retry."
        )
    if probe.state == "missing_host":
        raise RuntimeError(probe.detail)
    try:
        raise RuntimeError(
            "Samsung LAN remote failed. If the TV shows an approval prompt, allow the connection and retry."
        )
    except RuntimeError as exc:
        raise exc


class SmartThingsClient:
    def __init__(self, token: str) -> None:
        self._token = token.strip()
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            }
        )

    def list_tvs(self) -> list[TVEndpoint]:
        response = self._session.get("https://api.smartthings.com/v1/devices", timeout=10)
        response.raise_for_status()
        payload = response.json()
        items = payload.get("items", [])
        endpoints: list[TVEndpoint] = []
        for item in items:
            label = item.get("label") or item.get("name") or "SmartThings Device"
            manufacturer = item.get("manufacturerName") or ""
            device_type = item.get("deviceTypeName") or ""
            if "tv" not in label.lower() and "tv" not in device_type.lower() and "samsung" not in manufacturer.lower():
                continue

            endpoints.append(
                TVEndpoint(
                    endpoint_id=_endpoint_id(smartthings_device_id=item["deviceId"]),
                    name=label,
                    manufacturer=manufacturer or "Samsung",
                    model_name=item.get("deviceTypeName"),
                    smartthings_device_id=item["deviceId"],
                    source="SmartThings",
                    metadata={"device_id": item["deviceId"]},
                )
            )
        endpoints.sort(key=lambda endpoint: endpoint.name.lower())
        return endpoints

    def send_command(self, device_id: str, capability: str, command: str, arguments: list | None = None) -> None:
        body = {
            "commands": [
                {
                    "component": "main",
                    "capability": capability,
                    "command": command,
                    "arguments": arguments or [],
                }
            ]
        }
        response = self._session.post(
            f"https://api.smartthings.com/v1/devices/{device_id}/commands",
            json=body,
            timeout=10,
        )
        response.raise_for_status()
