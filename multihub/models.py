from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class SourceItem:
    source_id: str
    path: Path | None
    name: str
    mime_type: str
    demo: bool = False


@dataclass(slots=True)
class TVEndpoint:
    endpoint_id: str
    name: str
    nickname: str | None = None
    host: str | None = None
    manufacturer: str | None = None
    model_name: str | None = None
    av_transport_url: str | None = None
    rendering_control_url: str | None = None
    location_url: str | None = None
    samsung_remote_port: int | None = None
    samsung_remote_token: str | None = None
    smartthings_device_id: str | None = None
    chromecast_uuid: str | None = None
    chromecast_port: int | None = None
    web_receiver_enabled: bool = False
    receiver_id: str | None = None
    source: str = "TV"
    metadata: dict[str, str] = field(default_factory=dict)

    @property
    def can_play_dlna(self) -> bool:
        return bool(self.av_transport_url and self.host)

    @property
    def can_use_samsung_remote(self) -> bool:
        return bool(self.host and self.samsung_remote_port)

    @property
    def can_launch_tizen_app(self) -> bool:
        return bool(self.host)

    @property
    def can_remote_control(self) -> bool:
        return self.can_use_samsung_remote

    @property
    def has_chromecast(self) -> bool:
        return bool(self.chromecast_uuid and self.host)

    @property
    def chromecast_playback_blocked(self) -> bool:
        return self.metadata.get("chromecast_playback_blocked") == "true"

    @property
    def can_play_chromecast(self) -> bool:
        return self.has_chromecast and not self.chromecast_playback_blocked

    @property
    def can_use_web_receiver(self) -> bool:
        return bool(self.web_receiver_enabled and self.receiver_id)

    @property
    def can_play_media(self) -> bool:
        return self.can_launch_tizen_app or self.can_play_chromecast

    @property
    def primary_host(self) -> str | None:
        return self.host

    @property
    def display_name(self) -> str:
        nickname = (self.nickname or "").strip()
        return nickname or self.name

    def source_labels(self) -> list[str]:
        labels = [self.source] if self.source else []
        extra = self.metadata.get("discovery_sources", "")
        if extra:
            labels.extend([item for item in extra.split("|") if item])

        deduped: list[str] = []
        for label in labels:
            if label and label not in deduped:
                deduped.append(label)
        return deduped

    def capability_labels(self) -> list[str]:
        labels: list[str] = []
        if self.can_launch_tizen_app:
            labels.append("Tizen app")
        if self.can_use_samsung_remote:
            labels.append("Samsung remote")
        if self.can_play_chromecast:
            labels.append("Chromecast")
        elif self.has_chromecast and self.chromecast_playback_blocked:
            labels.append("Chromecast blocked")
        if self.smartthings_device_id:
            labels.append("SmartThings")
        return labels

    def merge_from(self, other: TVEndpoint) -> None:
        for attr in (
            "host",
            "manufacturer",
            "model_name",
            "av_transport_url",
            "rendering_control_url",
            "location_url",
            "samsung_remote_port",
            "samsung_remote_token",
            "smartthings_device_id",
            "chromecast_uuid",
            "chromecast_port",
            "receiver_id",
        ):
            current = getattr(self, attr)
            incoming = getattr(other, attr)
            if not current and incoming:
                setattr(self, attr, incoming)

        if other.web_receiver_enabled:
            self.web_receiver_enabled = True

        if (not self.name or self.name.startswith("Unknown")) and other.name:
            self.name = other.name
        if not self.nickname and other.nickname:
            self.nickname = other.nickname
        if (not self.manufacturer or self.manufacturer == "Unknown") and other.manufacturer:
            self.manufacturer = other.manufacturer
        if (not self.model_name or self.model_name == "Unknown") and other.model_name:
            self.model_name = other.model_name

        merged_sources = self.source_labels()
        for label in other.source_labels():
            if label not in merged_sources:
                merged_sources.append(label)
        if merged_sources:
            self.source = merged_sources[0]
            self.metadata["discovery_sources"] = "|".join(merged_sources[1:])

        for key, value in other.metadata.items():
            current = self.metadata.get(key)
            if key not in self.metadata or ((current is None or current == "") and value not in (None, "")):
                self.metadata[key] = value
