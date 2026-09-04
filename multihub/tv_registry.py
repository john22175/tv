from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import TVEndpoint


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TVStateRegistry:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (Path.cwd() / "tv_registry.json")
        self._data = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 2, "updated_at": _utc_now(), "tvs": {}, "stage_setups": {}}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"version": 2, "updated_at": _utc_now(), "tvs": {}, "stage_setups": {}}
        if not isinstance(payload, dict):
            return {"version": 2, "updated_at": _utc_now(), "tvs": {}, "stage_setups": {}}
        payload.setdefault("version", 2)
        payload.setdefault("updated_at", _utc_now())
        payload.setdefault("tvs", {})
        payload.setdefault("stage_setups", {})
        return payload

    def save(self) -> None:
        self._data["updated_at"] = _utc_now()
        self.path.write_text(json.dumps(self._data, indent=2, sort_keys=True), encoding="utf-8")

    def _key_for_endpoint(self, endpoint: TVEndpoint) -> str:
        if endpoint.host:
            return endpoint.host
        if endpoint.smartthings_device_id:
            return f"smartthings:{endpoint.smartthings_device_id}"
        return endpoint.endpoint_id

    def endpoint_key(self, endpoint: TVEndpoint) -> str:
        return self._key_for_endpoint(endpoint)

    def upsert_endpoint(self, endpoint: TVEndpoint) -> dict[str, Any]:
        key = self._key_for_endpoint(endpoint)
        tvs = self._data.setdefault("tvs", {})
        record = tvs.setdefault(key, {})
        record.update(
            {
                "key": key,
                "endpoint_id": endpoint.endpoint_id,
                "name": endpoint.name,
                "nickname": endpoint.nickname,
                "host": endpoint.host,
                "manufacturer": endpoint.manufacturer,
                "model_name": endpoint.model_name,
                "av_transport_url": endpoint.av_transport_url,
                "rendering_control_url": endpoint.rendering_control_url,
                "location_url": endpoint.location_url,
                "samsung_remote_port": endpoint.samsung_remote_port,
                "samsung_remote_token": endpoint.samsung_remote_token,
                "smartthings_device_id": endpoint.smartthings_device_id,
                "chromecast_uuid": endpoint.chromecast_uuid,
                "chromecast_port": endpoint.chromecast_port,
                "receiver_id": endpoint.receiver_id,
                "web_receiver_enabled": endpoint.web_receiver_enabled,
                "sources": endpoint.source_labels(),
                "capabilities": {
                    "tizen_app": endpoint.can_launch_tizen_app,
                    "samsung_remote": endpoint.can_use_samsung_remote,
                    "chromecast": endpoint.can_play_chromecast,
                    "chromecast_present": endpoint.has_chromecast,
                    "smartthings": bool(endpoint.smartthings_device_id),
                },
                "metadata": dict(endpoint.metadata),
                "updated_at": _utc_now(),
            }
        )
        return record

    def list_endpoints(self) -> list[TVEndpoint]:
        items = self._data.get("tvs", {})
        if not isinstance(items, dict):
            return []

        endpoints: list[TVEndpoint] = []
        for record in items.values():
            if not isinstance(record, dict):
                continue

            metadata = dict(record.get("metadata", {})) if isinstance(record.get("metadata"), dict) else {}
            sources = record.get("sources", [])
            if isinstance(sources, list):
                cleaned_sources = [str(item) for item in sources if item]
            else:
                cleaned_sources = []
            if cleaned_sources:
                metadata["discovery_sources"] = "|".join(cleaned_sources)

            endpoint_id = str(record.get("endpoint_id") or record.get("host") or record.get("key") or "")
            if not endpoint_id:
                continue

            endpoint = TVEndpoint(
                endpoint_id=endpoint_id,
                name=str(record.get("name") or record.get("host") or "Saved TV"),
                nickname=str(record["nickname"]) if record.get("nickname") else None,
                host=str(record["host"]) if record.get("host") else None,
                manufacturer=str(record["manufacturer"]) if record.get("manufacturer") else None,
                model_name=str(record["model_name"]) if record.get("model_name") else None,
                av_transport_url=str(record["av_transport_url"]) if record.get("av_transport_url") else None,
                rendering_control_url=str(record["rendering_control_url"]) if record.get("rendering_control_url") else None,
                location_url=str(record["location_url"]) if record.get("location_url") else None,
                samsung_remote_port=int(record["samsung_remote_port"]) if record.get("samsung_remote_port") else None,
                samsung_remote_token=str(record["samsung_remote_token"]) if record.get("samsung_remote_token") else None,
                smartthings_device_id=str(record["smartthings_device_id"]) if record.get("smartthings_device_id") else None,
                chromecast_uuid=str(record["chromecast_uuid"]) if record.get("chromecast_uuid") else None,
                chromecast_port=int(record["chromecast_port"]) if record.get("chromecast_port") else None,
                web_receiver_enabled=bool(record.get("web_receiver_enabled")),
                receiver_id=str(record["receiver_id"]) if record.get("receiver_id") else None,
                source="Registry",
                metadata=metadata,
            )
            endpoints.append(endpoint)

        endpoints.sort(key=lambda endpoint: endpoint.display_name.lower())
        return endpoints

    def list_stage_setups(self) -> list[dict[str, Any]]:
        setups = self._data.get("stage_setups", {})
        if not isinstance(setups, dict):
            return []

        records: list[dict[str, Any]] = []
        for name, payload in setups.items():
            if not isinstance(payload, dict):
                continue
            records.append(
                {
                    "name": str(payload.get("name") or name),
                    "markers": list(payload.get("markers", [])) if isinstance(payload.get("markers"), list) else [],
                    "updated_at": str(payload.get("updated_at") or ""),
                }
            )
        records.sort(key=lambda record: record["name"].lower())
        return records

    def get_stage_setup(self, name: str) -> dict[str, Any] | None:
        setups = self._data.get("stage_setups", {})
        if not isinstance(setups, dict):
            return None
        payload = setups.get(name)
        return payload if isinstance(payload, dict) else None

    def save_stage_setup(self, name: str, markers: list[dict[str, Any]], **extra_fields: Any) -> None:
        setups = self._data.setdefault("stage_setups", {})
        payload = {
            "name": name,
            "markers": markers,
            "updated_at": _utc_now(),
        }
        payload.update(extra_fields)
        setups[name] = payload
        self.save()

    def delete_stage_setup(self, name: str) -> bool:
        setups = self._data.get("stage_setups", {})
        if not isinstance(setups, dict) or name not in setups:
            return False
        del setups[name]
        self.save()
        return True

    def record_control_state(
        self,
        endpoint: TVEndpoint,
        control_name: str,
        state: str,
        detail: str,
        *,
        port: int | None = None,
        response_event: str | None = None,
    ) -> None:
        record = self.upsert_endpoint(endpoint)
        controls = record.setdefault("control", {})
        control_record = controls.setdefault(control_name, {})
        timestamp = _utc_now()
        control_record.update(
            {
                "state": state,
                "detail": detail,
                "updated_at": timestamp,
            }
        )
        if port is not None:
            control_record["port"] = port
        if response_event:
            control_record["response_event"] = response_event
        if state in {"authorized", "success"}:
            control_record["last_success_at"] = timestamp
        self.save()

    def record_playback_state(self, endpoint: TVEndpoint, route: str, state: str, detail: str) -> None:
        record = self.upsert_endpoint(endpoint)
        playback = record.setdefault("playback", {})
        playback[route] = {
            "state": state,
            "detail": detail,
            "updated_at": _utc_now(),
        }
        self.save()

    def get_record(self, endpoint: TVEndpoint) -> dict[str, Any] | None:
        return self._data.get("tvs", {}).get(self._key_for_endpoint(endpoint))

    def control_summary(self, endpoint: TVEndpoint) -> str:
        record = self.get_record(endpoint)
        if not record:
            return "None"
        controls = record.get("control", {})
        playback = record.get("playback", {})
        parts: list[str] = []

        samsung_remote = controls.get("samsung_remote")
        if isinstance(samsung_remote, dict) and samsung_remote.get("state"):
            port = samsung_remote.get("port")
            port_text = f" on {port}" if port else ""
            parts.append(f"Samsung remote: {samsung_remote['state']}{port_text}")

        for route in ("tizen_app", "chromecast"):
            route_state = playback.get(route)
            if isinstance(route_state, dict) and route_state.get("state"):
                parts.append(f"{route}: {route_state['state']}")

        return ", ".join(parts) if parts else "None"
