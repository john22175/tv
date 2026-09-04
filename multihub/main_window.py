from __future__ import annotations

import mimetypes
import os
import subprocess
import sys
import time
import traceback
import uuid
import hashlib
from pathlib import Path

from PyQt6.QtCore import QEvent, QObject, QPoint, QRunnable, QSignalBlocker, Qt, QThreadPool, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QDoubleSpinBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QDialog,
    QInputDialog,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .connectors import (
    SmartThingsClient,
    chromecast_support_status,
    detect_local_subnet,
    discover_chromecast_tvs,
    discover_samsung_lan_tvs,
    open_tizen_receiver_app,
    pause_chromecast,
    play_to_chromecast,
    probe_samsung_lan_tv,
    probe_samsung_remote_access,
)
from .media_server import MediaHTTPServer, OfflineLibrarySource, ServedMedia
from .models import SourceItem, TVEndpoint
from .tv_registry import TVStateRegistry
from .widgets import HubStage, SourceListWidget, card_shadow


SUPPORTED_UPLOADS = (
    "Media Files (*.mp4 *.mov *.m4v *.webm *.mp3 *.wav *.jpg *.jpeg *.png *.gif *.bmp *.pdf *.ppt *.pptx);;"
    "All Files (*.*)"
)
ROUTE_MODE_AUTO = "auto"
ROUTE_MODE_TIZEN = "tizen_app"
ROUTE_MODE_CHROMECAST = "chromecast"
OFFSET_PRELOAD_DELAY_SECONDS = 5.0
DEFAULT_STAGE_SEQUENCE_DELAY_SECONDS = 5.0
RECEIVER_LAUNCH_CONFIRM_TIMEOUT_SECONDS = 15.0


def infer_mime_type(path: Path) -> str:
    mime_type = mimetypes.guess_type(path.name)[0]
    return mime_type or "application/octet-stream"


class WorkerSignals(QObject):
    result = pyqtSignal(object)
    error = pyqtSignal(str)
    finished = pyqtSignal()


class Worker(QRunnable):
    def __init__(self, func, *args, **kwargs) -> None:
        super().__init__()
        self.func = func
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()

    def run(self) -> None:
        try:
            result = self.func(*self.args, **self.kwargs)
        except Exception:
            self.signals.error.emit(traceback.format_exc())
        else:
            self.signals.result.emit(result)
        finally:
            self.signals.finished.emit()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Samsung Smart TV MultiHub")
        self.resize(1560, 1020)
        self.thread_pool = QThreadPool(self)
        self.media_server = MediaHTTPServer()
        self.media_server.start()
        self.tv_registry = TVStateRegistry()

        self.sources: dict[str, SourceItem] = {}
        self.endpoints: dict[str, TVEndpoint] = {}
        self.discovery_buckets: dict[str, dict[str, TVEndpoint]] = {
            "Registry": {},
            "Samsung LAN": {},
            "Chromecast": {},
            "SmartThings": {},
        }
        self.stage_marker_endpoints: dict[str, str | None] = {}
        self.endpoint_to_marker: dict[str, str] = {}
        self.marker_source_ids: dict[str, str | None] = {}
        self.marker_start_offsets: dict[str, float] = {}
        self.stage_sequence_order: list[str] = []
        self.stage_sequence_delay_seconds = DEFAULT_STAGE_SEQUENCE_DELAY_SECONDS
        self.loaded_source_paths: set[str] = set()
        self.device_counter = 0
        self.marker_counter = 0
        self.last_source_id: str | None = None
        self.selected_marker_id: str | None = None
        self.selected_marker_ids: set[str] = set()
        self.stage_remote_dialog: QDialog | None = None
        self._syncing_endpoint_from_stage = False
        self._receiver_playback_tokens: dict[str, int] = {}
        self._scheduled_receiver_start_tokens: dict[str, int] = {}
        self._offline_library_revision = ""

        self._build_ui()
        self._rebuild_stage_setup_list()
        self._bootstrap_stage_defaults()
        self._refresh_offline_library()
        self._set_styles()
        self._configure_chromecast_ui()
        self._load_registry_tvs(quiet=True)
        self._library_sync_refresh_timer = QTimer(self)
        self._library_sync_refresh_timer.setInterval(1000)
        self._library_sync_refresh_timer.timeout.connect(self._refresh_selected_library_sync_hint)
        self._library_sync_refresh_timer.start()
        self._set_status(f"Media server ready at {self.media_server.base_url}")

    def closeEvent(self, event) -> None:
        self.media_server.stop()
        super().closeEvent(event)

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)

        shell = QVBoxLayout(root)
        shell.setContentsMargins(18, 18, 18, 18)
        shell.setSpacing(16)

        self.source_list_controls = SourceListWidget()
        self.source_list_controls.preview_requested.connect(self._handle_source_preview)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("mainTabs")
        self.discover_tab = self._wrap_scroll_tab(self._build_discover_tab())
        self.manage_tab = self._wrap_scroll_tab(self._build_manage_tab())
        self.stage_tab = self._build_stage_tab()
        self.tabs.addTab(self.discover_tab, "Discover")
        self.tabs.addTab(self.manage_tab, "Tizen Receiver")
        self.tabs.addTab(self.stage_tab, "Device Stage")
        shell.addWidget(self.tabs, 1)

        status_panel = self._panel()
        status_layout = QHBoxLayout(status_panel)
        status_layout.setContentsMargins(18, 14, 18, 14)
        self.status_label = QLabel("Ready")
        self.status_label.setObjectName("statusBarLabel")
        status_layout.addWidget(self.status_label)
        status_layout.addStretch(1)
        self.target_chip = QLabel("No TV selected")
        self.target_chip.setObjectName("targetChip")
        status_layout.addWidget(self.target_chip)
        shell.addWidget(status_panel)

    def _build_discover_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        discovery_panel = self._panel()
        discovery_layout = QVBoxLayout(discovery_panel)
        discovery_layout.setSpacing(10)
        discovery_layout.addWidget(self._section_header("TV Discovery", "Scan Samsung LAN, Chromecast, optional SmartThings endpoints, or reload saved TVs from the registry."))

        discovery_buttons = QGridLayout()
        discovery_buttons.setHorizontalSpacing(10)
        discovery_buttons.setVerticalSpacing(10)
        self.samsung_button = QPushButton("Discover Samsung LAN")
        self.samsung_button.clicked.connect(self._discover_samsung_lan_tvs)
        self.chromecast_button = QPushButton("Discover Chromecast")
        self.chromecast_button.clicked.connect(self._discover_chromecast_tvs)
        self.saved_tvs_button = QPushButton("Load Saved TVs")
        self.saved_tvs_button.clicked.connect(self._load_registry_tvs)
        discovery_buttons.addWidget(self.samsung_button, 0, 0)
        discovery_buttons.addWidget(self.chromecast_button, 0, 1)
        discovery_buttons.addWidget(self.saved_tvs_button, 1, 0, 1, 2)
        discovery_layout.addLayout(discovery_buttons)

        manual_row = QHBoxLayout()
        self.manual_samsung_host = QLineEdit()
        self.manual_samsung_host.setPlaceholderText("Manual Samsung TV IP, e.g. 10.171.64.125")
        self.manual_samsung_button = QPushButton("Add Samsung IP")
        self.manual_samsung_button.clicked.connect(self._add_manual_samsung_ip)
        manual_row.addWidget(self.manual_samsung_host, 1)
        manual_row.addWidget(self.manual_samsung_button)
        discovery_layout.addLayout(manual_row)

        self.smartthings_token = QLineEdit()
        self.smartthings_token.setPlaceholderText("SmartThings Personal Access Token")
        self.smartthings_button = QPushButton("Load SmartThings TVs")
        self.smartthings_button.clicked.connect(self._load_smartthings_tvs)
        discovery_layout.addWidget(self.smartthings_token)
        discovery_layout.addWidget(self.smartthings_button)
        layout.addWidget(discovery_panel)

        routing_panel = self._panel()
        routing_layout = QVBoxLayout(routing_panel)
        routing_layout.setSpacing(8)
        routing_layout.addWidget(
            self._section_header(
                "Routing Validation",
                "Validate the selected TV's Developer Mode IP, SDB link, receiver package, and media-server route before sending sources.",
            )
        )
        self.validate_route_button = QPushButton("Validate Selected TV Route")
        self.validate_route_button.clicked.connect(self._validate_selected_tv_route)
        routing_layout.addWidget(self.validate_route_button)
        self.validate_all_routes_button = QPushButton("Validate All Loaded TV Routes")
        self.validate_all_routes_button.clicked.connect(self._validate_all_tv_routes)
        routing_layout.addWidget(self.validate_all_routes_button)
        self.routing_validation_result = QLabel(
            "Select a TV on the Tizen Receiver tab, then run validation. The receiver uses the current desktop media-server address and ignores stale stored addresses."
        )
        self.routing_validation_result.setObjectName("mutedCopy")
        self.routing_validation_result.setWordWrap(True)
        routing_layout.addWidget(self.routing_validation_result)
        layout.addWidget(routing_panel)
        layout.addStretch(1)
        return tab

    def _validate_selected_tv_route(self) -> None:
        endpoint = self._selected_endpoint()
        if endpoint is None:
            QMessageBox.warning(self, "Routing Validation", "Select a TV on the Tizen Receiver tab first.")
            return
        if not endpoint.host:
            QMessageBox.warning(self, "Routing Validation", "The selected TV has no network host/IP.")
            return

        self.validate_route_button.setEnabled(False)
        self.routing_validation_result.setText(f"Validating the route to {endpoint.display_name}...")
        self._set_status(f"Validating developer and receiver routing for {endpoint.display_name}...")
        self._run_task(
            lambda: self._collect_route_validation(endpoint),
            on_result=lambda result: self._show_route_validation(endpoint, result),
            on_error=lambda details: self._finish_route_validation_error(details),
        )

    def _validate_all_tv_routes(self) -> None:
        endpoints = sorted((endpoint for endpoint in self.endpoints.values() if endpoint.host), key=lambda item: item.display_name.lower())
        if not endpoints:
            QMessageBox.warning(self, "Routing Validation", "Load or discover TV endpoints first.")
            return
        self.validate_route_button.setEnabled(False)
        self.validate_all_routes_button.setEnabled(False)
        self.routing_validation_result.setText(f"Validating routes for {len(endpoints)} loaded TV(s)...")
        self._run_task(
            lambda: [(endpoint, self._collect_route_validation(endpoint)) for endpoint in endpoints],
            on_result=self._show_all_route_validation,
            on_error=lambda details: self._finish_route_validation_error(details),
        )

    def _collect_route_validation(self, endpoint: TVEndpoint) -> dict[str, object]:
        fresh = probe_samsung_lan_tv(endpoint.host or "", timeout=4.0)
        desktop_url = self.media_server.base_url
        desktop_ip = desktop_url.split("//", 1)[-1].split(":", 1)[0]
        developer_ip = fresh.metadata.get("developer_ip", "") if fresh else ""
        developer_mode = fresh.metadata.get("developer_mode", "") if fresh else ""
        sdb_path = "C:/tizen-studio/tools/sdb.exe" if os.path.exists("C:/tizen-studio/tools/sdb.exe") else "sdb"
        serial = f"{endpoint.host}:26101"
        try:
            connected = subprocess.run([sdb_path, "connect", endpoint.host or ""], capture_output=True, text=True, timeout=20)
            devices = subprocess.run([sdb_path, "devices"], capture_output=True, text=True, timeout=15)
            applist = subprocess.run([sdb_path, "-s", serial, "shell", "0", "applist"], capture_output=True, text=True, timeout=20)
            sdb_ready = connected.returncode == 0 and serial in (devices.stdout or "")
            receiver_installed = "MHubRcvr01.MultiHubReceiver" in (applist.stdout or "")
        except (OSError, subprocess.TimeoutExpired):
            sdb_ready = False
            receiver_installed = False

        return {
            "desktop_url": desktop_url,
            "desktop_ip": desktop_ip,
            "developer_ip": developer_ip,
            "developer_mode": developer_mode,
            "api_reachable": fresh is not None,
            "sdb_ready": sdb_ready,
            "receiver_installed": receiver_installed,
        }

    def _show_route_validation(self, endpoint: TVEndpoint, result: dict[str, object]) -> None:
        self.validate_route_button.setEnabled(True)
        self.validate_all_routes_button.setEnabled(True)
        desktop_ip = str(result["desktop_ip"])
        developer_ip = str(result["developer_ip"])
        matches = bool(developer_ip) and developer_ip == desktop_ip
        lines = [
            f"TV: {endpoint.display_name} ({endpoint.host})",
            f"Samsung API: {'reachable' if result['api_reachable'] else 'not reachable'}",
            f"Desktop media server: {result['desktop_url']}",
            f"Developer Mode: {'on' if result['developer_mode'] == '1' else 'off or unavailable'}",
            f"Developer Mode PC: {developer_ip or 'not reported'} {'✓' if matches else '— must match the desktop IP'}",
            f"SDB link: {'ready' if result['sdb_ready'] else 'not ready'}",
            f"MultiHub Receiver package: {'installed' if result['receiver_installed'] else 'not detected'}",
            "Receiver storage policy: the packaged desktop address takes priority over stale TV storage.",
        ]
        self.routing_validation_result.setText("\n".join(lines))
        self._set_status(f"Routing validation finished for {endpoint.display_name}.")

    def _show_all_route_validation(self, results) -> None:
        self.validate_route_button.setEnabled(True)
        self.validate_all_routes_button.setEnabled(True)
        rows: list[str] = []
        working = 0
        for endpoint, result in results:
            developer_matches = result["developer_ip"] == result["desktop_ip"]
            ready = bool(result["api_reachable"] and developer_matches and result["sdb_ready"] and result["receiver_installed"])
            working += int(ready)
            rows.append(
                f"{'✓' if ready else '!' } {endpoint.display_name}: API {'OK' if result['api_reachable'] else 'fail'}, "
                f"Dev IP {result['developer_ip'] or 'missing'}, SDB {'OK' if result['sdb_ready'] else 'fail'}, "
                f"receiver {'OK' if result['receiver_installed'] else 'missing'}"
            )
        self.routing_validation_result.setText(
            f"{working}/{len(results)} loaded TV route(s) fully ready.\n" + "\n".join(rows)
        )
        self._set_status(f"Validated {len(results)} loaded TV route(s): {working} ready.")

    def _finish_route_validation_error(self, details: str) -> None:
        self.validate_route_button.setEnabled(True)
        self.validate_all_routes_button.setEnabled(True)
        self.routing_validation_result.setText(f"Routing validation failed: {self._error_summary(details)}")
        self._set_status("Routing validation failed.")

    def _build_manage_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        endpoint_panel = self._panel()
        endpoint_layout = QVBoxLayout(endpoint_panel)
        endpoint_layout.setSpacing(8)
        endpoint_layout.addWidget(self._section_header("Step 1: Select TV", "Choose the TV you want to drive through the MultiHub Tizen receiver app. Discovery methods still merge into one device entry."))
        self.endpoint_list = QListWidget()
        self.endpoint_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.endpoint_list.itemSelectionChanged.connect(self._endpoint_selection_changed)
        self.endpoint_list.itemDoubleClicked.connect(self._endpoint_double_clicked)
        endpoint_layout.addWidget(self.endpoint_list, 1)
        self.endpoint_details = QLabel("No endpoint selected.")
        self.endpoint_details.setWordWrap(True)
        self.endpoint_details.setObjectName("mutedCopy")
        endpoint_layout.addWidget(self.endpoint_details)
        nickname_row = QHBoxLayout()
        nickname_row.setSpacing(8)
        self.nickname_field = QLineEdit()
        self.nickname_field.setPlaceholderText("Nickname for selected TV")
        self.save_nickname_button = QPushButton("Save Nickname")
        self.save_nickname_button.clicked.connect(self._save_selected_tv_nickname)
        nickname_row.addWidget(self.nickname_field, 1)
        nickname_row.addWidget(self.save_nickname_button)
        endpoint_layout.addLayout(nickname_row)
        self.receiver_url_field = QLineEdit()
        self.receiver_url_field.setReadOnly(True)
        self.receiver_url_field.setPlaceholderText("Receiver state URL appears here for the selected TV")
        endpoint_layout.addWidget(self.receiver_url_field)
        self.library_sync_hint = QLabel("Offline library: select a TV to view its sync status.")
        self.library_sync_hint.setObjectName("mutedCopy")
        self.library_sync_hint.setWordWrap(True)
        endpoint_layout.addWidget(self.library_sync_hint)
        self.tizen_flow_hint = QLabel(
            "Tizen flow: Discover TVs -> select one TV -> open the receiver app -> send the current source."
        )
        self.tizen_flow_hint.setObjectName("mutedCopy")
        self.tizen_flow_hint.setWordWrap(True)
        endpoint_layout.addWidget(self.tizen_flow_hint)
        duid_row = QHBoxLayout()
        duid_row.setSpacing(8)
        self.duid_field = QLineEdit()
        self.duid_field.setReadOnly(True)
        self.duid_field.setPlaceholderText("DUID appears here for the selected TV")
        self.copy_duid_button = QPushButton("Copy DUID")
        self.copy_duid_button.clicked.connect(self._copy_duid)
        self.probe_tv_info_button = QPushButton("Probe TV Info")
        self.probe_tv_info_button.clicked.connect(self._probe_selected_tv_info)
        duid_row.addWidget(self.duid_field, 1)
        duid_row.addWidget(self.copy_duid_button)
        duid_row.addWidget(self.probe_tv_info_button)
        endpoint_layout.addLayout(duid_row)
        endpoint_layout.addWidget(self._section_header("Stage Setups", "Save or load named stage layouts with TV assignments and drag positions."))
        self.stage_setup_name_field = QLineEdit()
        self.stage_setup_name_field.setPlaceholderText("Stage setup name")
        endpoint_layout.addWidget(self.stage_setup_name_field)
        stage_setup_actions = QGridLayout()
        stage_setup_actions.setHorizontalSpacing(8)
        stage_setup_actions.setVerticalSpacing(8)
        self.stage_setup_save_button = QPushButton("Save New")
        self.stage_setup_save_button.clicked.connect(lambda: self._save_stage_setup(overwrite=False))
        self.stage_setup_update_button = QPushButton("Save Over")
        self.stage_setup_update_button.clicked.connect(lambda: self._save_stage_setup(overwrite=True))
        self.stage_setup_load_button = QPushButton("Load Setup")
        self.stage_setup_load_button.clicked.connect(self._load_selected_stage_setup)
        self.stage_setup_delete_button = QPushButton("Delete Setup")
        self.stage_setup_delete_button.clicked.connect(self._delete_selected_stage_setup)
        stage_setup_actions.addWidget(self.stage_setup_save_button, 0, 0)
        stage_setup_actions.addWidget(self.stage_setup_update_button, 0, 1)
        stage_setup_actions.addWidget(self.stage_setup_load_button, 1, 0)
        stage_setup_actions.addWidget(self.stage_setup_delete_button, 1, 1)
        endpoint_layout.addLayout(stage_setup_actions)
        self.stage_setup_list = QListWidget()
        self.stage_setup_list.setMaximumHeight(150)
        self.stage_setup_list.itemSelectionChanged.connect(self._stage_setup_selection_changed)
        self.stage_setup_list.itemDoubleClicked.connect(self._stage_setup_double_clicked)
        endpoint_layout.addWidget(self.stage_setup_list)
        layout.addWidget(endpoint_panel, 1)

        actions_panel = self._panel()
        actions_layout = QVBoxLayout(actions_panel)
        actions_layout.setSpacing(8)
        actions_layout.addWidget(self._section_header("Step 2: Run Tizen Receiver", "Use this top-to-bottom path for normal playback. MultiHub will prefer the installed Tizen receiver app." ))

        primary_actions = QGridLayout()
        primary_actions.setHorizontalSpacing(8)
        primary_actions.setVerticalSpacing(8)
        self.open_receiver_button = QPushButton("1. Open Receiver App On TV")
        self.open_receiver_button.clicked.connect(self._open_receiver_on_tv)
        self.send_selected_button = QPushButton("2. Send Current Source To TV")
        self.send_selected_button.clicked.connect(self._send_selected_source)
        self.pause_playback_button = QPushButton("3. Pause Receiver Playback")
        self.pause_playback_button.clicked.connect(self._pause_remote_playback)
        self.copy_receiver_button = QPushButton("Copy Receiver URL")
        self.copy_receiver_button.clicked.connect(self._copy_receiver_url)
        self.sync_library_button = QPushButton("Retry Offline Library Sync")
        self.sync_library_button.clicked.connect(self._retry_offline_library_sync)
        self.send_stage_button = QPushButton("Send Current Source To Stage TVs")
        self.send_stage_button.clicked.connect(self._send_source_to_stage_tvs)
        self.send_all_tvs_button = QPushButton("Send Current Source To All TVs")
        self.send_all_tvs_button.clicked.connect(self._send_selected_source_to_all_tvs)
        self.route_mode_combo = QComboBox()
        self.route_mode_combo.addItem("Tizen Receiver (Recommended)", ROUTE_MODE_TIZEN)
        self.route_mode_combo.addItem("Auto Fallback", ROUTE_MODE_AUTO)
        self.route_mode_combo.addItem("Chromecast Only", ROUTE_MODE_CHROMECAST)
        self.route_mode_combo.setCurrentIndex(0)
        self.route_mode_combo.setToolTip("Default to the Tizen receiver app. Switch only when you need a fallback route.")
        primary_actions.addWidget(self.open_receiver_button, 0, 0, 1, 2)
        primary_actions.addWidget(self.send_selected_button, 1, 0, 1, 2)
        primary_actions.addWidget(self.pause_playback_button, 2, 0, 1, 2)
        primary_actions.addWidget(self.copy_receiver_button, 3, 0, 1, 2)
        primary_actions.addWidget(self.sync_library_button, 4, 0, 1, 2)
        primary_actions.addWidget(self.route_mode_combo, 5, 0, 1, 2)

        actions_layout.addLayout(primary_actions)

        secondary_actions_header = self._section_header("Stage And Bulk", "Use these only when you want to fan out one source to several TVs.")
        actions_layout.addWidget(secondary_actions_header)
        secondary_actions = QGridLayout()
        secondary_actions.setHorizontalSpacing(8)
        secondary_actions.setVerticalSpacing(8)
        secondary_actions.addWidget(self.send_stage_button, 0, 0)
        secondary_actions.addWidget(self.send_all_tvs_button, 0, 1)
        self.load_selected_tvs_button = QPushButton("Load Selected To Stage")
        self.load_selected_tvs_button.clicked.connect(self._load_selected_endpoints_to_stage)
        self.load_all_tvs_button = QPushButton("Load All To Stage")
        self.load_all_tvs_button.clicked.connect(self._load_all_endpoints_to_stage)
        secondary_actions.addWidget(self.load_selected_tvs_button, 1, 0)
        secondary_actions.addWidget(self.load_all_tvs_button, 1, 1)
        actions_layout.addLayout(secondary_actions)

        actions_layout.addWidget(self._section_header("Troubleshooting", "Only use these if the Tizen receiver app does not open or the TV needs to be re-paired."))
        self.probe_access_button = QPushButton("Probe Samsung Access")
        self.probe_access_button.clicked.connect(self._probe_selected_tv_access)
        self.unblock_chromecast_button = QPushButton("Unblock Chromecast")
        self.unblock_chromecast_button.clicked.connect(self._unblock_chromecast_for_selected)
        troubleshooting_actions = QGridLayout()
        troubleshooting_actions.setHorizontalSpacing(8)
        troubleshooting_actions.setVerticalSpacing(8)
        troubleshooting_actions.addWidget(self.probe_access_button, 0, 0)
        troubleshooting_actions.addWidget(self.unblock_chromecast_button, 0, 1)
        actions_layout.addLayout(troubleshooting_actions)
        layout.addWidget(actions_panel)
        self._set_manage_action_state(0)
        return tab

    def _build_stage_tab(self) -> QWidget:
        tab = QWidget()
        layout = QHBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        tray_panel = self._panel()
        tray_panel.setMinimumWidth(320)
        tray_layout = QVBoxLayout(tray_panel)
        tray_layout.setSpacing(10)
        tray_layout.addWidget(self._section_header("Stage Sources", "Use this tray when dragging sources directly onto the large TV surface or square TV blurbs."))

        stage_source_actions = QGridLayout()
        stage_source_actions.setHorizontalSpacing(10)
        stage_source_actions.setVerticalSpacing(10)
        self.stage_upload_button = QPushButton("Upload Source Files")
        self.stage_upload_button.clicked.connect(self._choose_files)
        self.stage_delete_source_button = QPushButton("Delete Selected Source")
        self.stage_delete_source_button.clicked.connect(self._delete_selected_source)
        stage_source_actions.addWidget(self.stage_upload_button, 0, 0)
        stage_source_actions.addWidget(self.stage_delete_source_button, 0, 1)
        tray_layout.addLayout(stage_source_actions)

        self.source_list_stage = SourceListWidget()
        self.source_list_stage.preview_requested.connect(self._handle_source_preview)
        tray_layout.addWidget(self.source_list_stage, 1)

        stage_buttons = QGridLayout()
        stage_buttons.setHorizontalSpacing(8)
        stage_buttons.setVerticalSpacing(8)
        self.add_marker_button = QPushButton("Add Free Marker")
        self.add_marker_button.clicked.connect(self._add_generic_marker)
        self.remove_selected_stage_button = QPushButton("Delete Selected Marker")
        self.remove_selected_stage_button.clicked.connect(self._remove_selected_stage_marker)
        stage_buttons.addWidget(self.add_marker_button, 0, 0)
        stage_buttons.addWidget(self.remove_selected_stage_button, 0, 1)
        tray_layout.addLayout(stage_buttons)

        self.device_summary = QLabel("0 stage markers active")
        self.device_summary.setObjectName("mutedCopy")
        tray_layout.addWidget(self.device_summary)
        tray_layout.addWidget(
            self._section_header(
                "Stage Playback",
                "Order the TVs on stage and use one delay to place each TV on the same paused repeating timeline.",
            )
        )

        stage_playback_actions = QGridLayout()
        stage_playback_actions.setHorizontalSpacing(8)
        stage_playback_actions.setVerticalSpacing(8)
        self.stage_send_all_button = QPushButton("Send Selected To Stage TVs")
        self.stage_send_all_button.clicked.connect(self._send_source_to_stage_tvs)
        self.stage_sequence_delay_spin = QDoubleSpinBox()
        self.stage_sequence_delay_spin.setRange(0.1, 86400.0)
        self.stage_sequence_delay_spin.setDecimals(1)
        self.stage_sequence_delay_spin.setSingleStep(0.5)
        self.stage_sequence_delay_spin.setSuffix(" s")
        self.stage_sequence_delay_spin.setValue(self.stage_sequence_delay_seconds)
        self.stage_sequence_delay_spin.valueChanged.connect(self._handle_stage_sequence_delay_changed)
        stage_playback_actions.addWidget(self.stage_send_all_button, 0, 0, 1, 2)
        stage_playback_actions.addWidget(QLabel("Delay between TVs"), 1, 0)
        stage_playback_actions.addWidget(self.stage_sequence_delay_spin, 1, 1)
        tray_layout.addLayout(stage_playback_actions)

        self.stage_sequence_list = QListWidget()
        self.stage_sequence_list.setObjectName("stageSequenceList")
        self.stage_sequence_list.setDragEnabled(True)
        self.stage_sequence_list.setAcceptDrops(True)
        self.stage_sequence_list.setDropIndicatorShown(True)
        self.stage_sequence_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.stage_sequence_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.stage_sequence_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.stage_sequence_list.setMinimumHeight(180)
        self.stage_sequence_list.model().rowsMoved.connect(self._handle_stage_sequence_reordered)
        tray_layout.addWidget(self.stage_sequence_list)

        self.stage_sequence_summary = QLabel("Load TVs onto the device stage to build a playback sequence.")
        self.stage_sequence_summary.setObjectName("mutedCopy")
        self.stage_sequence_summary.setWordWrap(True)
        tray_layout.addWidget(self.stage_sequence_summary)
        tray_layout.addStretch(1)
        self.remove_selected_stage_button.setVisible(False)

        tray_scroll = QScrollArea()
        tray_scroll.setWidgetResizable(True)
        tray_scroll.setFrameShape(QFrame.Shape.NoFrame)
        tray_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        tray_scroll.setWidget(tray_panel)
        tray_scroll.setMinimumWidth(340)
        tray_scroll.setMaximumWidth(380)
        layout.addWidget(tray_scroll, 0)

        stage_shell = QWidget()
        stage_layout = QVBoxLayout(stage_shell)
        stage_layout.setContentsMargins(0, 0, 0, 0)
        stage_layout.setSpacing(14)
        stage_heading = QHBoxLayout()
        heading_copy = QVBoxLayout()
        top_copy = QLabel("LIVE STAGE")
        top_copy.setObjectName("eyebrow")
        top_title = QLabel("Living Room Device Stage")
        top_title.setObjectName("stageTitle")
        heading_copy.addWidget(top_copy)
        heading_copy.addWidget(top_title)
        stage_heading.addLayout(heading_copy)
        stage_heading.addStretch(1)
        zoom_row = QHBoxLayout()
        zoom_row.setSpacing(8)
        self.stage_remote_button = QPushButton("Remote")
        self.stage_remote_button.setObjectName("stageRemoteLauncher")
        self.stage_remote_button.clicked.connect(self._show_stage_remote_popup)
        self.stage_zoom_out_button = QPushButton("-")
        self.stage_zoom_out_button.clicked.connect(lambda: self._adjust_stage_zoom(-0.1))
        self.stage_zoom_out_button.setFixedSize(32, 28)
        self.stage_zoom_reset_button = QPushButton("100%")
        self.stage_zoom_reset_button.clicked.connect(lambda: self._set_stage_zoom(1.0))
        self.stage_zoom_reset_button.setFixedSize(56, 28)
        self.stage_zoom_in_button = QPushButton("+")
        self.stage_zoom_in_button.clicked.connect(lambda: self._adjust_stage_zoom(0.1))
        self.stage_zoom_in_button.setFixedSize(32, 28)
        self.stage_zoom_fit_button = QPushButton("Fit")
        self.stage_zoom_fit_button.clicked.connect(self._fit_stage_zoom)
        self.stage_zoom_fit_button.setFixedSize(42, 28)
        zoom_row.addWidget(self.stage_remote_button)
        zoom_row.addWidget(self.stage_zoom_out_button)
        zoom_row.addWidget(self.stage_zoom_reset_button)
        zoom_row.addWidget(self.stage_zoom_in_button)
        zoom_row.addWidget(self.stage_zoom_fit_button)
        stage_heading.addLayout(zoom_row)
        stage_layout.addLayout(stage_heading)

        self.hub_stage = HubStage()
        self.hub_stage.set_zoom(0.85)
        self.hub_stage.marker_source_dropped.connect(self._handle_marker_source_drop)
        self.hub_stage.marker_selected.connect(self._handle_marker_selected)
        self.hub_stage.background_clicked.connect(self._clear_marker_selection)
        self.stage_scroll = QScrollArea()
        self.stage_scroll.setWidgetResizable(False)
        self.stage_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.stage_scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.stage_scroll.setWidget(self.hub_stage)
        self.stage_scroll.viewport().installEventFilter(self)
        stage_layout.addWidget(self.stage_scroll, 1)
        layout.addWidget(stage_shell, 1)
        self._sync_stage_zoom_ui()
        self.stage_remote_button.setEnabled(False)
        return tab

    def _wrap_scroll_tab(self, content: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setWidget(content)
        return scroll

    def _configure_chromecast_ui(self) -> None:
        available, message = chromecast_support_status()
        self.chromecast_button.setEnabled(available)
        self.chromecast_button.setToolTip(message)
        if not available:
            self.chromecast_button.setText("Chromecast Unavailable")

    def eventFilter(self, watched, event) -> bool:
        stage_scroll = getattr(self, "stage_scroll", None)
        if (
            stage_scroll is not None
            and watched is stage_scroll.viewport()
            and event.type() == QEvent.Type.Wheel
            and event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            delta = event.angleDelta().y()
            if delta:
                self._adjust_stage_zoom(0.1 if delta > 0 else -0.1)
                return True
        return super().eventFilter(watched, event)

    def _set_styles(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget {
                background: #141414;
                color: #ececec;
                font-family: "Segoe UI";
                font-size: 13px;
            }
            QFrame#panel {
                background: #1c1c1c;
                border: 1px solid #2b2b2b;
                border-radius: 18px;
            }
            QTabWidget::pane {
                border: 1px solid #282828;
                border-radius: 16px;
                top: -1px;
                background: #121212;
            }
            QScrollArea {
                background: transparent;
                border: none;
            }
            QScrollBar:vertical {
                background: #161616;
                width: 10px;
                margin: 4px 4px 4px 0;
                border-radius: 5px;
            }
            QScrollBar::handle:vertical {
                background: #4a4a4a;
                min-height: 24px;
                border-radius: 5px;
            }
            QScrollBar::handle:vertical:hover {
                background: #5a5a5a;
            }
            QScrollBar:horizontal {
                background: #161616;
                height: 10px;
                margin: 0 4px 4px 4px;
                border-radius: 5px;
            }
            QScrollBar::handle:horizontal {
                background: #4a4a4a;
                min-width: 24px;
                border-radius: 5px;
            }
            QScrollBar::handle:horizontal:hover {
                background: #5a5a5a;
            }
            QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {
                background: transparent;
                border: none;
            }
            QTabBar::tab {
                background: #1a1a1a;
                color: #a8a8a8;
                border: 1px solid #2d2d2d;
                border-bottom: none;
                padding: 10px 16px;
                min-width: 140px;
                border-top-left-radius: 12px;
                border-top-right-radius: 12px;
                margin-right: 4px;
            }
            QTabBar::tab:selected {
                background: #262626;
                color: #f0f0f0;
            }
            QLabel#eyebrow {
                color: #9a9a9a;
                font-size: 11px;
                font-weight: 700;
                letter-spacing: 0.18em;
            }
            QLabel#brandTitle {
                font-size: 30px;
                font-weight: 700;
            }
            QLabel#mutedCopy, QLabel#sectionCopy, QLabel#statusBarLabel {
                color: #9b9b9b;
            }
            QLabel#sectionTitle {
                font-size: 16px;
                font-weight: 700;
            }
            QLabel#stageTitle {
                font-size: 28px;
                font-weight: 700;
            }
            QPushButton {
                background: #2b2b2b;
                color: #f2f2f2;
                border: 1px solid #3a3a3a;
                border-radius: 10px;
                padding: 8px 12px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #343434;
                border-color: #4a4a4a;
            }
            QPushButton:pressed {
                background: #202020;
            }
            QLineEdit {
                background: #171717;
                border: 1px solid #303030;
                border-radius: 10px;
                padding: 8px 10px;
                color: #ececec;
            }
            QListWidget {
                background: #181818;
                border: 1px solid #2f2f2f;
                border-radius: 12px;
                padding: 8px;
            }
            QListWidget::item {
                border: none;
                margin: 0 0 4px 0;
                padding: 2px;
            }
            QListWidget::item:selected {
                background: #2f2f2f;
                border-radius: 8px;
            }
            QFrame#sourceCard {
                background: #202020;
                border: 1px solid #303030;
                border-radius: 14px;
            }
            QLabel#sourceBadge {
                background: #303030;
                color: #f0f0f0;
                border-radius: 12px;
                font-weight: 700;
            }
            QLabel#sourceName {
                font-size: 14px;
                font-weight: 700;
            }
            QLabel#sourceMeta {
                color: #9b9b9b;
            }
            QFrame#hubStage {
                background: #121212;
                border: 1px solid #282828;
                border-radius: 26px;
            }
            QFrame#tvFrame {
                background: #1f1f1f;
                border-radius: 24px;
                border: 1px solid #343434;
            }
            QLabel#tvHeader {
                color: #aaaaaa;
                font-size: 12px;
            }
            QFrame#tvSurface {
                background: #090909;
                border-radius: 16px;
                border: 1px solid #2c2c2c;
            }
            QFrame#tvSurface[dropActive="true"] {
                border: 2px solid #6a6a6a;
            }
            QWidget#tvPlaceholder {
                background: transparent;
            }
            QFrame#documentCard {
                background: #111111;
                border: 1px solid #262626;
                border-radius: 12px;
            }
            QLabel#documentTitle {
                font-size: 24px;
                font-weight: 700;
            }
            QLabel#documentCopy {
                color: #b8b8b8;
                font-size: 14px;
                max-width: 560px;
            }
            QCheckBox {
                spacing: 8px;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border-radius: 4px;
                border: 1px solid #4b4b4b;
                background: #171717;
            }
            QCheckBox::indicator:checked {
                background: #208dff;
                border: 1px solid #208dff;
            }
            QFrame#deviceNode {
                background: #1b1b1b;
                border: 1px solid #303030;
                border-radius: 16px;
            }
            QFrame#deviceNode[dropActive="true"] {
                border: 2px solid #208dff;
            }
            QFrame#deviceNode[selected="true"] {
                border: 2px solid #208dff;
                background: #232323;
            }
            QLabel#devicePreview {
                background: #262626;
                border: 1px solid #353535;
                border-radius: 14px;
                color: #dcdcdc;
                font-size: 20px;
                font-weight: 700;
            }
            QLabel#deviceTitle {
                font-size: 12px;
                font-weight: 700;
            }
            QLabel#deviceSubtitle {
                font-size: 11px;
                color: #9b9b9b;
            }
            QLabel#targetChip {
                background: #202020;
                border: 1px solid #333333;
                border-radius: 14px;
                padding: 8px 12px;
                color: #d8d8d8;
                font-weight: 600;
            }
            QPushButton#stageRemoteLauncher {
                min-width: 86px;
            }
            QDialog#stageRemoteDialog {
                background: #151515;
                border: 1px solid #2d2d2d;
                border-radius: 18px;
            }
            QLabel#stageRemoteTitle {
                font-size: 15px;
                font-weight: 700;
            }
            QLabel#stageRemoteTarget {
                color: #aaaaaa;
                font-size: 12px;
            }
            QPushButton#stageRemoteKeyButton,
            QPushButton#stageRemotePrimaryButton {
                min-width: 64px;
                min-height: 38px;
            }
            QPushButton#stageRemotePrimaryButton {
                font-weight: 700;
                border-color: #208dff;
            }
            """
        )

    def _panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("panel")
        card_shadow(panel)
        return panel

    def _section_header(self, title: str, copy: str) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        heading = QLabel(title)
        heading.setObjectName("sectionTitle")
        detail = QLabel(copy)
        detail.setObjectName("sectionCopy")
        detail.setWordWrap(True)
        layout.addWidget(heading)
        layout.addWidget(detail)
        return box

    def _bootstrap_stage_defaults(self) -> None:
        self._autoload_default_sources()

    def _default_sources_dir(self) -> Path:
        preferred = Path.cwd() / "Sources"
        return preferred if preferred.exists() else Path.home()

    def _normalize_source_path(self, path: Path) -> str:
        try:
            return str(path.resolve()).lower()
        except OSError:
            return str(path).lower()

    def _autoload_default_sources(self) -> None:
        directory = self._default_sources_dir()
        if not directory.exists() or not directory.is_dir() or directory == Path.home():
            return

        for path in sorted(directory.iterdir(), key=lambda item: item.name.lower()):
            if not path.is_file():
                continue
            self._register_path_source(path, quiet=True)

    @staticmethod
    def _source_content_hash(path: Path) -> str:
        """Fast revision fingerprint for startup sync without reading multi-gigabyte media."""
        stat = path.stat()
        fingerprint = f"{path.name.casefold()}\0{stat.st_size}\0{stat.st_mtime_ns}".encode("utf-8")
        return hashlib.sha256(fingerprint).hexdigest()

    def _refresh_offline_library(self) -> str:
        """Mirror the same top-level Sources set that is auto-loaded at startup."""
        directory = self._default_sources_dir()
        entries: list[OfflineLibrarySource] = []
        if directory.exists() and directory.is_dir() and directory != Path.home():
            for path in sorted(directory.iterdir(), key=lambda item: item.name.casefold()):
                if not path.is_file():
                    continue
                try:
                    mime_type = infer_mime_type(path)
                    entries.append(
                        OfflineLibrarySource(
                            item_id=path.name.casefold(),
                            name=path.name,
                            mime_type=mime_type,
                            size=path.stat().st_size,
                            content_hash=self._source_content_hash(path),
                            path=path,
                            playable=mime_type.startswith(("image/", "video/", "audio/")),
                        )
                    )
                except OSError as exc:
                    self._set_status(f"Skipped offline library source {path.name}: {self._error_summary(str(exc))}")
        self._offline_library_revision = self.media_server.set_offline_library(entries)
        return self._offline_library_revision

    def _request_offline_library_sync(self, endpoints: list[TVEndpoint]) -> int:
        eligible = [endpoint for endpoint in endpoints if endpoint.can_launch_tizen_app]
        for endpoint in eligible:
            self.media_server.receiver_url(
                self._ensure_receiver_id(endpoint),
                self._preferred_receiver_alias(endpoint),
                endpoint.host,
            )
        self.media_server.request_offline_library_sync([self._ensure_receiver_id(endpoint) for endpoint in eligible])
        return len(eligible)

    def _retry_offline_library_sync(self) -> None:
        endpoints = self._selected_endpoints()
        if not endpoints:
            QMessageBox.warning(self, "Offline Library", "Select one or more TVs first.")
            return
        revision = self._refresh_offline_library()
        requested = self._request_offline_library_sync(endpoints)
        self._endpoint_selection_changed()
        self._set_status(
            f"Requested offline library revision {revision[:8]} from {requested} selected receiver app(s). "
            "Open a pending receiver app on its TV to complete the sync."
        )

    def _library_sync_summary(self, endpoint: TVEndpoint) -> str:
        status = self.media_server.receiver_library_status(self._ensure_receiver_id(endpoint))
        if status is None:
            return "not requested"
        detail = status.detail.strip()
        if detail and len(detail) > 140:
            detail = f"{detail[:137]}..."
        return f"{status.state} ({status.stored_bytes / (1024 * 1024):.1f} MB): {detail}"

    def _refresh_selected_library_sync_hint(self) -> None:
        """Keep the selected receiver's asynchronous library status visible without reselection."""
        selected = self._selected_endpoints()
        endpoint = self._selected_endpoint()
        if len(selected) == 1 and endpoint is not None:
            self.library_sync_hint.setText(f"Offline library: {self._library_sync_summary(endpoint)}")

    def _choose_files(self) -> None:
        file_names, _ = QFileDialog.getOpenFileNames(self, "Upload Source Files", str(self._default_sources_dir()), SUPPORTED_UPLOADS)
        for file_name in file_names:
            self._register_path_source(Path(file_name))

    def _register_path_source(self, path: Path, *, quiet: bool = False) -> bool:
        candidate = path.expanduser()
        if not candidate.exists():
            self._report_source_load_failure(candidate, "The selected file no longer exists.", quiet=quiet)
            return False
        if not candidate.is_file():
            self._report_source_load_failure(candidate, "Only files can be added as sources.", quiet=quiet)
            return False

        try:
            return self._register_source(
                SourceItem(
                    source_id=f"source-{uuid.uuid4().hex}",
                    path=candidate,
                    name=candidate.name,
                    mime_type=infer_mime_type(candidate),
                )
            )
        except Exception as exc:
            self._report_source_load_failure(candidate, self._error_summary(str(exc) or repr(exc)), quiet=quiet)
            return False

    def _register_source(self, source: SourceItem) -> bool:
        normalized: str | None = None
        if source.path is not None:
            normalized = self._normalize_source_path(source.path)
            if normalized in self.loaded_source_paths:
                return False
            self.loaded_source_paths.add(normalized)
        self.sources[source.source_id] = source
        try:
            self.source_list_controls.add_source(source)
            self.source_list_stage.add_source(source)
        except Exception:
            self.sources.pop(source.source_id, None)
            if normalized is not None:
                self.loaded_source_paths.discard(normalized)
            raise
        self.last_source_id = source.source_id
        return True

    def _source_for_path(self, path: Path) -> SourceItem | None:
        normalized = self._normalize_source_path(path)
        for source in self.sources.values():
            if source.path is None:
                continue
            if self._normalize_source_path(source.path) == normalized:
                return source
        return None

    def _restore_stage_source(self, saved_path: object) -> SourceItem | None:
        raw_path = str(saved_path or "").strip()
        if not raw_path:
            return None
        path = Path(raw_path).expanduser()
        if not path.exists() or not path.is_file():
            return None
        existing = self._source_for_path(path)
        if existing is not None:
            return existing
        if not self._register_path_source(path, quiet=True):
            return None
        return self._source_for_path(path)

    def _report_source_load_failure(self, path: Path, detail: str, *, quiet: bool) -> None:
        name = path.name or str(path)
        message = f"Failed to load source {name}: {detail}"
        self._set_status(message)
        if not quiet:
            QMessageBox.warning(self, "Load Source", message)

    def _delete_selected_source(self) -> None:
        source_id = self.source_list_controls.selected_source_id() or self.source_list_stage.selected_source_id()
        if source_id is None:
            QMessageBox.warning(self, "Delete Source", "Select a source first.")
            return
        self._remove_source(source_id)

    def _remove_source(self, source_id: str) -> None:
        source = self.sources.pop(source_id, None)
        if source is None:
            return

        if source.path is not None:
            self.loaded_source_paths.discard(self._normalize_source_path(source.path))

        self.source_list_controls.remove_source(source_id)
        self.source_list_stage.remove_source(source_id)

        if self.last_source_id == source_id:
            self.last_source_id = None

        for marker_id, current_source_id in list(self.marker_source_ids.items()):
            if current_source_id != source_id:
                continue
            node = self.hub_stage.node(marker_id)
            if node is not None:
                node.set_source(None)
            self.marker_source_ids[marker_id] = None

        self._set_status(f"Removed source: {source.name}")

    def _select_source_in_lists(self, source_id: str) -> None:
        for widget in (self.source_list_controls, self.source_list_stage):
            for index in range(widget.count()):
                item = widget.item(index)
                if item.data(Qt.ItemDataRole.UserRole) == source_id:
                    widget.setCurrentItem(item)
                    break

    def _current_source(self) -> SourceItem | None:
        source_id = self.source_list_controls.selected_source_id() or self.source_list_stage.selected_source_id() or self.last_source_id
        if not source_id:
            return None
        return self.sources.get(source_id)

    def _handle_source_preview(self, source_id: str) -> None:
        source = self.sources.get(source_id)
        if source:
            self.last_source_id = source_id
            self._select_source_in_lists(source_id)

    def _handle_source_drop(self, source_id: str) -> None:
        source = self.sources.get(source_id)
        if source is None:
            return

        self._cancel_stage_sequence()
        self.last_source_id = source_id
        self._select_source_in_lists(source_id)
        endpoint = self._selected_endpoint()
        if endpoint is None:
            self._set_status("Preview updated. Select a TV endpoint first, or drop the source on a square TV blurb linked to a device.")
            return
        self._send_source_to_endpoint(source, endpoint)

    def _handle_marker_source_drop(self, marker_id: str, source_id: str) -> None:
        source = self.sources.get(source_id)
        if source is None:
            return

        self._cancel_stage_sequence()
        self.last_source_id = source_id
        self._select_source_in_lists(source_id)
        self._set_marker_source(marker_id, source)
        self._handle_marker_selected(marker_id)

        endpoint_id = self.stage_marker_endpoints.get(marker_id)
        if not endpoint_id:
            self._set_status("Updated the free stage marker preview. Link a real TV to a marker if you want drag-to-play.")
            return

        endpoint = self.endpoints.get(endpoint_id)
        if endpoint is None:
            self._set_status("This stage marker is linked to a TV that is no longer available.")
            return
        self._send_source_to_endpoint(
            source,
            endpoint,
            marker_id=marker_id,
            start_offset_seconds=0.0,
            scheduled_playback_delay_seconds=0.0,
        )

    def _is_castable_media(self, source: SourceItem) -> bool:
        return source.mime_type.startswith(("video/", "image/", "audio/"))

    def _route_mode(self) -> str:
        return str(self.route_mode_combo.currentData() or ROUTE_MODE_AUTO)

    def _send_via_tizen_app(
        self,
        source: SourceItem,
        endpoint: TVEndpoint,
        served: ServedMedia,
        *,
        start_offset_seconds: float = 0.0,
        scheduled_playback_delay_seconds: float = 0.0,
    ) -> None:
        initial_playback_state = "paused" if scheduled_playback_delay_seconds > 0.0 else "playing"
        receiver_url = self._update_receiver_for_source(
            endpoint,
            source,
            served,
            start_offset_seconds=start_offset_seconds,
            playback_state=initial_playback_state,
        )
        self.receiver_url_field.setText(receiver_url)
        if scheduled_playback_delay_seconds > 0.0:
            self._set_status(
                f"Prepared {source.name} for {endpoint.display_name} at +{start_offset_seconds:.1f}s. "
                f"Launching the Tizen receiver app and starting in {scheduled_playback_delay_seconds:.0f} seconds..."
            )
        else:
            self._set_status(f"Prepared {source.name} for {endpoint.display_name}. Launching the Tizen receiver app...")
        self._launch_receiver_app(
            endpoint,
            source_name=source.name,
            receiver_url=receiver_url,
            show_failure_dialog=False,
            on_confirmed=(
                lambda: self._schedule_receiver_start(
                    endpoint,
                    source.name,
                    scheduled_playback_delay_seconds,
                )
                if scheduled_playback_delay_seconds > 0.0
                else None
            ),
        )

    def _launch_receiver_app(
        self,
        endpoint: TVEndpoint,
        *,
        source_name: str,
        receiver_url: str,
        failure_title: str = "Launch Tizen App",
        show_failure_dialog: bool = False,
        on_confirmed=None,
    ) -> bool:
        if not endpoint.can_launch_tizen_app:
            return False

        self._run_task(
            lambda: self._launch_tizen_receiver_and_wait(endpoint),
            on_result=lambda probe: self._handle_receiver_app_probe(
                endpoint,
                probe,
                receiver_url,
                source_name=source_name,
                failure_title=failure_title,
                show_failure_dialog=show_failure_dialog,
                on_confirmed=on_confirmed,
            ),
            on_error=lambda details: self._handle_receiver_app_error(
                endpoint,
                details,
                receiver_url,
                source_name=source_name,
                failure_title=failure_title,
                show_failure_dialog=show_failure_dialog,
            ),
        )
        return True

    def _launch_tizen_receiver_and_wait(self, endpoint: TVEndpoint):
        """Do not report success until the TV receiver has fetched its state."""
        receiver_id = self._ensure_receiver_id(endpoint)
        launch_started_at = time.time()
        probe = open_tizen_receiver_app(endpoint)
        if not probe.ok:
            return probe
        if self.media_server.wait_for_receiver_ready(
            receiver_id,
            after=launch_started_at,
            timeout=RECEIVER_LAUNCH_CONFIRM_TIMEOUT_SECONDS,
        ):
            probe.detail = "MultiHub Receiver app launched and connected to the media server."
            return probe
        probe.ok = False
        probe.state = "receiver_not_ready"
        probe.detail = (
            "The launch command was accepted, but the MultiHub Receiver app did not connect to the media server "
            f"within {RECEIVER_LAUNCH_CONFIRM_TIMEOUT_SECONDS:.0f} seconds."
        )
        return probe

    def _handle_receiver_app_probe(
        self,
        endpoint: TVEndpoint,
        probe,
        receiver_url: str,
        *,
        source_name: str,
        failure_title: str,
        show_failure_dialog: bool,
        on_confirmed,
    ) -> None:
        self._record_remote_probe(endpoint, probe)
        self._endpoint_selection_changed()
        if probe.ok:
            if on_confirmed is not None:
                on_confirmed()
            self._handle_playback_success(
                endpoint,
                "tizen_app",
                source_name,
                f"{source_name} is now targeted to {endpoint.display_name} through the Tizen receiver app.",
            )
            return

        self.tv_registry.record_playback_state(endpoint, "tizen_app", "failed", f"{source_name}: {probe.state}: {probe.detail}")
        if show_failure_dialog:
            self._handle_remote_probe(
                endpoint,
                probe,
                success_text=(
                    f"{source_name} is ready for {endpoint.display_name} in the Tizen receiver app."
                ),
                failure_title=failure_title,
            )
            return

        self._set_status(
            f"{source_name} is ready for {endpoint.display_name}, but the Tizen app launch failed ({probe.state}). "
            f"Receiver state remains staged at {receiver_url}."
        )

    def _handle_receiver_app_error(
        self,
        endpoint: TVEndpoint,
        details: str,
        receiver_url: str,
        *,
        source_name: str,
        failure_title: str,
        show_failure_dialog: bool,
    ) -> None:
        self.tv_registry.record_playback_state(endpoint, "tizen_app", "failed", f"{source_name}: {self._error_summary(details)}")
        if show_failure_dialog:
            self._format_error(failure_title)(details)
            return
        self._set_status(
            f"{source_name} is ready for {endpoint.display_name}, but the Tizen app launch failed. "
            f"Receiver state remains staged at {receiver_url}."
        )

    def _selected_endpoints(self) -> list[TVEndpoint]:
        selected: list[TVEndpoint] = []
        seen: set[str] = set()
        for item in self.endpoint_list.selectedItems():
            endpoint_id = item.data(Qt.ItemDataRole.UserRole)
            endpoint = self.endpoints.get(str(endpoint_id)) if endpoint_id else None
            if endpoint is None or endpoint.endpoint_id in seen:
                continue
            selected.append(endpoint)
            seen.add(endpoint.endpoint_id)
        return selected

    def _unblock_chromecast_for_selected(self) -> None:
        endpoints = self._selected_endpoints()
        if not endpoints:
            endpoint = self._selected_endpoint()
            endpoints = [endpoint] if endpoint is not None else []
        if not endpoints:
            QMessageBox.warning(self, "Unblock Chromecast", "Select one or more TV endpoints first.")
            return

        updated = 0
        for endpoint in endpoints:
            if "chromecast_playback_blocked" in endpoint.metadata:
                endpoint.metadata.pop("chromecast_playback_blocked", None)
                endpoint.metadata.pop("chromecast_playback_error", None)
                updated += 1
            elif endpoint.has_chromecast:
                updated += 1
            self.tv_registry.upsert_endpoint(endpoint)

        self.tv_registry.save()
        current = self._selected_endpoint()
        current_id = current.endpoint_id if current is not None else None
        self._rebuild_endpoint_list(current_id=current_id)
        self._refresh_stage_markers()
        self._endpoint_selection_changed()
        self._set_status(f"Chromecast unblocked for {updated} selected device(s).")

    def _save_selected_tv_nickname(self) -> None:
        endpoint = self._selected_endpoint()
        if endpoint is None:
            QMessageBox.warning(self, "Save Nickname", "Select a TV first.")
            return

        nickname = self.nickname_field.text().strip()
        endpoint.nickname = nickname or None
        self.tv_registry.upsert_endpoint(endpoint)
        self.tv_registry.save()
        self._rebuild_endpoint_list(current_id=endpoint.endpoint_id)
        self._refresh_stage_markers()
        self._select_endpoint_in_list(endpoint.endpoint_id)
        self._endpoint_selection_changed()
        label = endpoint.display_name
        self._set_status(f"Saved nickname for {label}.")

    def _stage_setup_selection_changed(self) -> None:
        item = self.stage_setup_list.currentItem()
        if item is None:
            return
        name = item.data(Qt.ItemDataRole.UserRole)
        if name:
            self.stage_setup_name_field.setText(str(name))

    def _stage_setup_double_clicked(self, item: QListWidgetItem) -> None:
        del item
        self._load_selected_stage_setup()

    def _rebuild_stage_setup_list(self, current_name: str | None = None) -> None:
        if not hasattr(self, "stage_setup_list"):
            return
        if current_name is None:
            current_item = self.stage_setup_list.currentItem()
            current_name = str(current_item.data(Qt.ItemDataRole.UserRole)) if current_item is not None else None

        self.stage_setup_list.clear()
        for setup in self.tv_registry.list_stage_setups():
            name = str(setup.get("name") or "")
            count = len(setup.get("markers", [])) if isinstance(setup.get("markers"), list) else 0
            item = QListWidgetItem(f"{name}  [{count} TV{'s' if count != 1 else ''}]")
            item.setData(Qt.ItemDataRole.UserRole, name)
            updated_at = str(setup.get("updated_at") or "")
            if updated_at:
                item.setToolTip(f"Updated {updated_at}")
            self.stage_setup_list.addItem(item)
            if current_name and name == current_name:
                self.stage_setup_list.setCurrentItem(item)

    def _current_stage_markers(self) -> list[dict[str, object]]:
        markers: list[dict[str, object]] = []
        for marker_id, endpoint_id in self.stage_marker_endpoints.items():
            node = self.hub_stage.node(marker_id)
            if node is None:
                continue
            endpoint = self.endpoints.get(endpoint_id) if endpoint_id else None
            markers.append(
                {
                    "endpoint_id": endpoint_id,
                    "endpoint_key": self.tv_registry.endpoint_key(endpoint) if endpoint is not None else None,
                    "title": node.title_label.text(),
                    "subtitle": node.subtitle_label.text(),
                    "sequence_order": self.stage_sequence_order.index(marker_id) if marker_id in self.stage_sequence_order else None,
                    "source_path": str(self.sources[source_id].path) if (source_id := self.marker_source_ids.get(marker_id)) and self.sources.get(source_id) and self.sources[source_id].path is not None else None,
                    "x": node.x(),
                    "y": node.y(),
                }
            )
        return markers

    def _save_stage_setup(self, *, overwrite: bool) -> None:
        name = self.stage_setup_name_field.text().strip()
        if not name:
            QMessageBox.warning(self, "Stage Setup", "Enter a stage setup name first.")
            return

        existing = self.tv_registry.get_stage_setup(name)
        if existing is not None and not overwrite:
            QMessageBox.warning(self, "Stage Setup", f"{name} already exists. Use Save Over to replace it.")
            return

        markers = self._current_stage_markers()
        self.tv_registry.save_stage_setup(
            name,
            markers,
            stage_sequence_delay_seconds=float(self.stage_sequence_delay_seconds),
        )
        self._rebuild_stage_setup_list(current_name=name)
        self._set_status(f"Saved stage setup: {name}.")

    def _delete_selected_stage_setup(self) -> None:
        item = self.stage_setup_list.currentItem()
        if item is None:
            QMessageBox.warning(self, "Delete Stage Setup", "Select a saved stage setup first.")
            return
        name = str(item.data(Qt.ItemDataRole.UserRole))
        if not self.tv_registry.delete_stage_setup(name):
            QMessageBox.warning(self, "Delete Stage Setup", f"{name} could not be removed.")
            return
        self.stage_setup_name_field.clear()
        self._rebuild_stage_setup_list()
        self._set_status(f"Deleted stage setup: {name}.")

    def _endpoint_for_registry_key(self, endpoint_key: str | None) -> TVEndpoint | None:
        if not endpoint_key:
            return None
        for endpoint in self.endpoints.values():
            if self.tv_registry.endpoint_key(endpoint) == endpoint_key:
                return endpoint
        return None

    def _clear_stage(self) -> None:
        self._cancel_stage_sequence()
        for marker_id in list(self.stage_marker_endpoints):
            self.hub_stage.remove_device_node(marker_id)
        self.stage_marker_endpoints.clear()
        self.endpoint_to_marker.clear()
        self.marker_source_ids.clear()
        self.marker_start_offsets.clear()
        self.stage_sequence_order.clear()
        self.marker_counter = 0
        self._clear_marker_selection()
        self._refresh_stage_summary()

    def _load_selected_stage_setup(self) -> None:
        item = self.stage_setup_list.currentItem()
        if item is None:
            QMessageBox.warning(self, "Load Stage Setup", "Select a saved stage setup first.")
            return

        name = str(item.data(Qt.ItemDataRole.UserRole))
        setup = self.tv_registry.get_stage_setup(name)
        markers = list(setup.get("markers", [])) if isinstance(setup, dict) else []
        self._clear_stage()
        loaded_sequence_order: list[tuple[int, str]] = []

        for marker in markers:
            if not isinstance(marker, dict):
                continue
            endpoint = self._endpoint_for_registry_key(str(marker.get("endpoint_key") or "")) or self.endpoints.get(str(marker.get("endpoint_id") or ""))
            position = QPoint(int(marker.get("x") or 36), int(marker.get("y") or 72))
            restored_source = self._restore_stage_source(marker.get("source_path"))
            if endpoint is not None:
                marker_id = self._ensure_endpoint_on_stage(endpoint, position=position)
                if restored_source is not None:
                    self._set_marker_source(marker_id, restored_source)
                if marker.get("sequence_order") is not None:
                    loaded_sequence_order.append((int(marker.get("sequence_order") or 0), marker_id))
                continue
            marker_id = self._add_stage_marker(
                endpoint_id=None,
                title=str(marker.get("title") or f"Marker {self.marker_counter + 1}"),
                subtitle=str(marker.get("subtitle") or "Saved TV unavailable"),
                position=position,
            )
            if restored_source is not None:
                self._set_marker_source(marker_id, restored_source)

        if loaded_sequence_order:
            self.stage_sequence_order = [marker_id for _, marker_id in sorted(loaded_sequence_order, key=lambda item: item[0])]
        self._sync_stage_sequence_order()
        if isinstance(setup, dict):
            self.stage_sequence_delay_seconds = max(0.1, float(setup.get("stage_sequence_delay_seconds") or DEFAULT_STAGE_SEQUENCE_DELAY_SECONDS))
            delay_blocker = QSignalBlocker(self.stage_sequence_delay_spin)
            self.stage_sequence_delay_spin.setValue(self.stage_sequence_delay_seconds)
            del delay_blocker
        self._refresh_stage_sequence_controls()

        stage_endpoints = self._stage_endpoints()
        self.tabs.setCurrentWidget(self.stage_tab)
        self._set_status(f"Loaded stage setup: {name}.")
        self._prime_stage_receiver_apps(stage_endpoints, context=f"stage setup {name}")

    def _selected_endpoint(self) -> TVEndpoint | None:
        item = self.endpoint_list.currentItem()
        if item is None:
            selected_items = self.endpoint_list.selectedItems()
            item = selected_items[0] if selected_items else None
        if item is None:
            return None
        endpoint_id = item.data(Qt.ItemDataRole.UserRole)
        if not endpoint_id:
            return None
        return self.endpoints.get(str(endpoint_id))

    def _send_selected_source(self) -> None:
        source = self._current_source()
        endpoint = self._selected_endpoint()
        if source is None:
            QMessageBox.warning(self, "Send Source", "Select a source first.")
            return
        if endpoint is None:
            QMessageBox.warning(self, "Send Source", "Select a TV endpoint first.")
            return
        self._cancel_stage_sequence()
        start_offset_seconds = self._prompt_start_offset_seconds(source)
        if start_offset_seconds is None:
            return
        self._send_source_to_endpoint(
            source,
            endpoint,
            start_offset_seconds=start_offset_seconds,
            scheduled_playback_delay_seconds=self._scheduled_playback_delay_seconds(source, start_offset_seconds),
        )

    def _send_selected_source_to_all_tvs(self) -> None:
        source = self._current_source()
        if source is None:
            QMessageBox.warning(self, "Send To All TVs", "Select a source first.")
            return

        self._cancel_stage_sequence()
        endpoints = sorted(self.endpoints.values(), key=lambda endpoint: endpoint.display_name.lower())
        if not endpoints:
            QMessageBox.warning(self, "Send To All TVs", "No TV endpoints are available.")
            return

        start_offset_seconds = self._prompt_start_offset_seconds(source)
        if start_offset_seconds is None:
            return

        self._set_status(f"Sending {source.name} to all {len(endpoints)} TV(s)...")
        scheduled_playback_delay_seconds = self._scheduled_playback_delay_seconds(source, start_offset_seconds)
        for endpoint in endpoints:
            marker_id = self.endpoint_to_marker.get(endpoint.endpoint_id)
            self._send_source_to_endpoint(
                source,
                endpoint,
                marker_id=marker_id,
                start_offset_seconds=start_offset_seconds,
                scheduled_playback_delay_seconds=scheduled_playback_delay_seconds,
            )

    def _send_source_to_stage_tvs(self) -> None:
        source = self._current_source()
        if source is None:
            QMessageBox.warning(self, "Send To Stage TVs", "Select a source first.")
            return

        endpoints = self._stage_endpoints()
        if not endpoints:
            QMessageBox.warning(self, "Send To Stage TVs", "No TVs are loaded on the device stage.")
            return

        if not source.mime_type.startswith("video/"):
            QMessageBox.warning(self, "Stage Loop", "The paused stage loop currently supports video sources only.")
            return
        targets = self._sequence_targets()
        if any(not endpoint.can_launch_tizen_app for _, endpoint in targets):
            QMessageBox.warning(self, "Stage Loop", "The paused stage loop requires the Tizen receiver app route on every staged TV.")
            return
        self._begin_stage_loop(source, targets)

    def _stage_endpoints(self) -> list[TVEndpoint]:
        endpoints: list[TVEndpoint] = []
        seen: set[str] = set()
        for endpoint_id in self.stage_marker_endpoints.values():
            if endpoint_id in seen:
                continue
            endpoint = self.endpoints.get(endpoint_id) if endpoint_id else None
            if endpoint is None:
                continue
            endpoints.append(endpoint)
            seen.add(endpoint_id)
        return endpoints

    def _stage_targets(self) -> list[tuple[str, TVEndpoint]]:
        targets: list[tuple[str, TVEndpoint]] = []
        seen: set[str] = set()
        for marker_id, endpoint_id in self.stage_marker_endpoints.items():
            if not endpoint_id or endpoint_id in seen:
                continue
            endpoint = self.endpoints.get(endpoint_id)
            if endpoint is None:
                continue
            targets.append((marker_id, endpoint))
            seen.add(endpoint_id)
        return targets

    def _sequence_targets(self) -> list[tuple[str, TVEndpoint]]:
        targets_by_marker = {marker_id: endpoint for marker_id, endpoint in self._stage_targets()}
        ordered: list[tuple[str, TVEndpoint]] = []
        seen: set[str] = set()
        for marker_id in self.stage_sequence_order:
            endpoint = targets_by_marker.get(marker_id)
            if endpoint is None or marker_id in seen:
                continue
            ordered.append((marker_id, endpoint))
            seen.add(marker_id)
        for marker_id, endpoint in self._stage_targets():
            if marker_id in seen:
                continue
            ordered.append((marker_id, endpoint))
            seen.add(marker_id)
        return ordered

    def _sync_stage_sequence_order(self) -> None:
        target_marker_ids = [marker_id for marker_id, _ in self._stage_targets()]
        if not target_marker_ids:
            self.stage_sequence_order = []
            return
        synced = [marker_id for marker_id in self.stage_sequence_order if marker_id in target_marker_ids]
        for marker_id in target_marker_ids:
            if marker_id not in synced:
                synced.append(marker_id)
        self.stage_sequence_order = synced

    def _stage_sequence_delay_seconds(self) -> float:
        return max(0.1, float(self.stage_sequence_delay_seconds))

    def _marker_start_offset(self, marker_id: str) -> float:
        return max(0.0, float(self.marker_start_offsets.get(marker_id, 0.0)))

    def _set_marker_start_offset(self, marker_id: str, start_offset_seconds: float) -> None:
        value = max(0.0, float(start_offset_seconds))
        self.marker_start_offsets[marker_id] = value
        node = self.hub_stage.node(marker_id)
        if node is not None:
            node.set_start_offset(value)
        self._refresh_stage_summary()

    def _source_supports_start_offsets(self, source: SourceItem) -> bool:
        return source.mime_type.startswith(("video/", "audio/"))

    def _scheduled_playback_delay_seconds(self, source: SourceItem, start_offset_seconds: float) -> float:
        if not source.mime_type.startswith("video/"):
            return 0.0
        if start_offset_seconds <= 0.0:
            return 0.0
        return OFFSET_PRELOAD_DELAY_SECONDS

    def _send_source_to_endpoint(
        self,
        source: SourceItem,
        endpoint: TVEndpoint,
        marker_id: str | None = None,
        *,
        start_offset_seconds: float = 0.0,
        scheduled_playback_delay_seconds: float = 0.0,
    ) -> str | None:
        if marker_id is None:
            marker_id = self.endpoint_to_marker.get(endpoint.endpoint_id)
        if marker_id:
            self._set_marker_source(marker_id, source)

        if source.path is None:
            self._set_status("Demo sources stay local. Upload a real media file to send it to a physical TV.")
            return None

        served = self.media_server.publish(source.path)
        route_mode = self._route_mode()
        if route_mode == ROUTE_MODE_TIZEN:
            if not endpoint.can_launch_tizen_app:
                self._set_status(f"Force Tizen App is selected, but {endpoint.display_name} has no Tizen app route.")
                return None
            self._send_via_tizen_app(
                source,
                endpoint,
                served,
                start_offset_seconds=start_offset_seconds,
                scheduled_playback_delay_seconds=scheduled_playback_delay_seconds,
            )
            return "tizen_app"

        if route_mode == ROUTE_MODE_CHROMECAST:
            if not self._is_castable_media(source):
                self._set_status(f"Force Chromecast is selected. {source.name} must be an image, video, or audio file.")
                return None
            if not endpoint.has_chromecast:
                self._set_status(f"Force Chromecast is selected, but {endpoint.display_name} has no Chromecast route.")
                return None
            self._set_status(f"Sending {source.name} to {endpoint.display_name} via forced Chromecast...")
            self._run_task(
                lambda: play_to_chromecast(
                    endpoint,
                    served.url,
                    served.mime_type,
                    source.name,
                    start_time_seconds=start_offset_seconds,
                ),
                on_result=lambda _: self._handle_playback_success(
                    endpoint,
                    "chromecast",
                    source.name,
                    f"Sent {source.name} to {endpoint.display_name} via forced Chromecast.",
                ),
                on_error=lambda details: self._handle_chromecast_send_error(endpoint, source, details),
            )
            return "chromecast"

        if endpoint.can_launch_tizen_app:
            self._send_via_tizen_app(
                source,
                endpoint,
                served,
                start_offset_seconds=start_offset_seconds,
                scheduled_playback_delay_seconds=scheduled_playback_delay_seconds,
            )
            return "tizen_app"

        if endpoint.can_play_chromecast and self._is_castable_media(source):
            self._set_status(f"Sending {source.name} to {endpoint.display_name} via Chromecast...")
            self._run_task(
                lambda: play_to_chromecast(
                    endpoint,
                    served.url,
                    served.mime_type,
                    source.name,
                    start_time_seconds=start_offset_seconds,
                ),
                on_result=lambda _: self._handle_playback_success(
                    endpoint,
                    "chromecast",
                    source.name,
                    f"Sent {source.name} to {endpoint.display_name} via Chromecast.",
                ),
                on_error=lambda details: self._handle_chromecast_send_error(endpoint, source, details),
            )
            return "chromecast"

        self._set_status(f"{endpoint.display_name} has no Tizen app or Chromecast route for {source.name}.")
        return None

    def _update_receiver_for_source(
        self,
        endpoint: TVEndpoint,
        source: SourceItem,
        served: ServedMedia | None,
        *,
        start_offset_seconds: float = 0.0,
        playback_state: str = "playing",
        playback_token: int | None = None,
        stage_session_id: str | None = "",
    ) -> str:
        receiver_id = self._ensure_receiver_id(endpoint)
        receiver_alias = self._preferred_receiver_alias(endpoint)
        library_item = self.media_server.library_item_for_path(source.path) if source.path is not None else None
        if source.mime_type.startswith(("image/", "video/", "audio/")) and served is not None:
            note = f"{source.name} is ready in MultiHub. Launch the Tizen receiver app on the TV to display it."
            media_url = served.url
        elif source.mime_type == "application/pdf":
            note = "PDF staged for the Tizen receiver app. Convert to video if you need direct playback behavior."
            media_url = None
        else:
            note = "Document staged for the Tizen receiver app. Convert presentations or documents to MP4 for direct playback."
            media_url = None

        receiver_url = self.media_server.update_receiver(
            receiver_id,
            source_name=source.name,
            mime_type=source.mime_type,
            media_url=media_url,
            note=note,
            playback_state=playback_state,
            start_position_seconds=start_offset_seconds,
            playback_token=playback_token,
            stage_session_id=stage_session_id,
            library_item_id=library_item.item_id if library_item is not None else "",
            library_content_hash=library_item.content_hash if library_item is not None else "",
            preferred_alias=receiver_alias,
            preferred_host=endpoint.host,
        )
        return receiver_url

    def _ensure_receiver_id(self, endpoint: TVEndpoint) -> str:
        if endpoint.receiver_id:
            return endpoint.receiver_id
        endpoint.receiver_id = f"receiver-{endpoint.endpoint_id}"
        return endpoint.receiver_id

    def _preferred_receiver_alias(self, endpoint: TVEndpoint) -> str | None:
        nickname = (endpoint.nickname or "").strip().lower()
        if nickname:
            return nickname
        if endpoint.host:
            tail = endpoint.host.rsplit(".", 1)[-1].strip()
            if tail:
                return tail
        name = endpoint.display_name.strip().lower()
        return name or None

    def _receiver_state_url(self, endpoint: TVEndpoint) -> str:
        if not endpoint.can_launch_tizen_app:
            return ""
        return self.media_server.receiver_url(
            self._ensure_receiver_id(endpoint),
            self._preferred_receiver_alias(endpoint),
            endpoint.host,
        )

    def _prepare_receiver_ready(self, endpoint: TVEndpoint) -> str:
        return self.media_server.update_receiver(
            self._ensure_receiver_id(endpoint),
            source_name="Receiver Ready",
            mime_type="text/plain",
            media_url=None,
            note="Launch the MultiHub Receiver app on the TV, then send a source from the desktop app.",
            playback_state="idle",
            start_position_seconds=0.0,
            preferred_alias=self._preferred_receiver_alias(endpoint),
            preferred_host=endpoint.host,
        )

    def _next_receiver_playback_token(self, endpoint: TVEndpoint) -> int:
        token = self._receiver_playback_tokens.get(endpoint.endpoint_id, 0) + 1
        self._receiver_playback_tokens[endpoint.endpoint_id] = token
        return token

    def _set_tizen_receiver_playback(
        self,
        endpoint: TVEndpoint,
        playback_state: str,
        *,
        start_position_seconds: float | None = None,
        playback_token: int | None = None,
    ) -> str:
        return self.media_server.set_receiver_playback(
            self._ensure_receiver_id(endpoint),
            playback_state=playback_state,
            start_position_seconds=start_position_seconds,
            playback_token=playback_token,
            preferred_alias=self._preferred_receiver_alias(endpoint),
            preferred_host=endpoint.host,
        )

    def _cancel_stage_sequence(self) -> None:
        """Compatibility hook for actions that replace staged media."""

    def _begin_stage_loop(self, source: SourceItem, targets: list[tuple[str, TVEndpoint]]) -> None:
        if not targets:
            return
        if source.path is None:
            QMessageBox.warning(self, "Stage Loop", "Upload a real video file before preparing the stage loop.")
            return

        stage_session_id = uuid.uuid4().hex
        delay_seconds = self._stage_sequence_delay_seconds()
        served = self.media_server.publish(source.path)
        self._set_status(
            f"Preparing {source.name} on {len(targets)} stage TV(s), paused at their delay offsets. Press Select, Enter, Pause, or Play on any TV remote to start all TVs together."
        )
        for sequence_number, (marker_id, endpoint) in enumerate(targets, start=1):
            start_offset_seconds = delay_seconds * (sequence_number - 1)
            self._set_marker_source(marker_id, source)
            self._set_marker_start_offset(marker_id, start_offset_seconds)
            receiver_url = self._update_receiver_for_source(
                endpoint,
                source,
                served,
                start_offset_seconds=start_offset_seconds,
                playback_state="paused",
                playback_token=self._next_receiver_playback_token(endpoint),
                stage_session_id=stage_session_id,
            )
            self.receiver_url_field.setText(receiver_url)
            self._launch_receiver_app(
                endpoint,
                source_name=source.name,
                receiver_url=receiver_url,
                show_failure_dialog=False,
            )

    def _schedule_receiver_start(self, endpoint: TVEndpoint, source_name: str, delay_seconds: float) -> None:
        token = self._scheduled_receiver_start_tokens.get(endpoint.endpoint_id, 0) + 1
        self._scheduled_receiver_start_tokens[endpoint.endpoint_id] = token
        QTimer.singleShot(
            max(0, int(delay_seconds * 1000)),
            lambda endpoint_id=endpoint.endpoint_id, token=token, source_name=source_name: self._resume_scheduled_receiver_playback(
                endpoint_id,
                token,
                source_name,
            ),
        )

    def _resume_scheduled_receiver_playback(self, endpoint_id: str, token: int, source_name: str) -> None:
        if self._scheduled_receiver_start_tokens.get(endpoint_id) != token:
            return
        self._scheduled_receiver_start_tokens.pop(endpoint_id, None)
        endpoint = self.endpoints.get(endpoint_id)
        if endpoint is None or not endpoint.can_launch_tizen_app:
            return
        self._set_tizen_receiver_playback(endpoint, "playing")
        self._set_status(f"Started {source_name} on {endpoint.display_name} after preload.")

    def _prompt_start_offset_seconds(self, source: SourceItem) -> float | None:
        if not source.mime_type.startswith(("video/", "audio/")):
            return 0.0
        value, accepted = QInputDialog.getDouble(
            self,
            "Video Start Offset",
            f"Start {source.name} at this offset in seconds:",
            0.0,
            0.0,
            86400.0,
            1,
        )
        if not accepted:
            return None
        return float(value)

    def _preferred_pause_route(self, endpoint: TVEndpoint) -> str | None:
        record = self.tv_registry.get_record(endpoint)
        playback = record.get("playback", {}) if isinstance(record, dict) else {}

        latest_route: str | None = None
        latest_updated_at = ""
        for route in ("tizen_app", "chromecast"):
            route_state = playback.get(route)
            if not isinstance(route_state, dict):
                continue
            if route_state.get("state") != "success":
                continue
            updated_at = str(route_state.get("updated_at") or "")
            if updated_at >= latest_updated_at:
                latest_updated_at = updated_at
                latest_route = route

        if latest_route == "tizen_app" and endpoint.can_launch_tizen_app:
            return latest_route
        if latest_route == "chromecast" and endpoint.can_play_chromecast:
            return latest_route
        if endpoint.can_launch_tizen_app:
            return "tizen_app"
        if endpoint.can_play_chromecast:
            return "chromecast"
        return None

    def _discover_samsung_lan_tvs(self) -> None:
        subnet = detect_local_subnet()
        self._set_status(f"Scanning Samsung LAN APIs on {subnet}...")
        self._run_task(
            lambda: discover_samsung_lan_tvs(subnet=subnet),
            on_result=lambda endpoints: self._apply_discovery_bucket("Samsung LAN", endpoints, "Samsung LAN discovery finished"),
            on_error=self._format_error("Samsung LAN discovery failed"),
        )

    def _discover_chromecast_tvs(self) -> None:
        available, message = chromecast_support_status()
        if not available:
            QMessageBox.warning(self, "Chromecast Support", message)
            self._set_status("Chromecast discovery unavailable in the current Python environment.")
            return
        self._set_status("Searching the local network for Chromecast devices...")
        self._run_task(
            discover_chromecast_tvs,
            on_result=lambda endpoints: self._apply_discovery_bucket("Chromecast", endpoints, "Chromecast discovery finished"),
            on_error=self._format_error("Chromecast discovery failed"),
        )

    def _load_registry_tvs(self, quiet: bool = False) -> None:
        endpoints = self.tv_registry.list_endpoints()
        self.discovery_buckets["Registry"] = {endpoint.endpoint_id: endpoint for endpoint in endpoints}
        self._rebuild_endpoints()
        requested = self._request_offline_library_sync(list(self.endpoints.values()))
        self._rebuild_stage_setup_list()
        if not quiet:
            self._set_status(
                f"Loaded {len(endpoints)} TV endpoint(s) from tv_registry.json and requested offline sync from {requested} receiver app(s)."
            )

    def _add_manual_samsung_ip(self) -> None:
        host = self.manual_samsung_host.text().strip()
        if not host:
            QMessageBox.warning(self, "Add Samsung IP", "Enter a Samsung TV IP address first.")
            return
        self._set_status(f"Probing Samsung LAN API at {host}...")
        from .connectors import probe_samsung_lan_tv

        self._run_task(
            lambda: probe_samsung_lan_tv(host),
            on_result=lambda endpoint: self._apply_manual_samsung_result(host, endpoint),
            on_error=self._format_error("Samsung IP probe failed"),
        )

    def _apply_manual_samsung_result(self, host: str, endpoint: TVEndpoint | None) -> None:
        if endpoint is None:
            QMessageBox.warning(self, "Add Samsung IP", f"No Samsung LAN API was detected at {host}.")
            return

        bucket = self.discovery_buckets["Samsung LAN"]
        bucket[endpoint.endpoint_id] = endpoint
        self._rebuild_endpoints()
        self._set_status(f"Added Samsung LAN TV at {host}.")

    def _load_smartthings_tvs(self) -> None:
        token = self.smartthings_token.text().strip()
        if not token:
            QMessageBox.warning(self, "SmartThings", "Enter a SmartThings Personal Access Token first.")
            return
        self._set_status("Loading SmartThings devices...")
        self._run_task(
            lambda: SmartThingsClient(token).list_tvs(),
            on_result=lambda endpoints: self._apply_discovery_bucket("SmartThings", endpoints, "Loaded SmartThings TVs"),
            on_error=self._format_error("SmartThings load failed"),
        )

    def _apply_discovery_bucket(self, label: str, endpoints: list[TVEndpoint], status_prefix: str) -> None:
        self.discovery_buckets[label] = {endpoint.endpoint_id: endpoint for endpoint in endpoints}
        self._rebuild_endpoints()
        self._set_status(f"{status_prefix}. Found {len(endpoints)} {label} endpoint(s).")

    def _rebuild_endpoints(self) -> None:
        current_id = None
        current_item = self.endpoint_list.currentItem()
        if current_item is not None:
            current_id = current_item.data(Qt.ItemDataRole.UserRole)
        selected_ids = {
            str(item.data(Qt.ItemDataRole.UserRole))
            for item in self.endpoint_list.selectedItems()
            if item.data(Qt.ItemDataRole.UserRole)
        }

        combined: dict[str, TVEndpoint] = {}
        for bucket in self.discovery_buckets.values():
            for endpoint in bucket.values():
                self._upsert_endpoint(combined, endpoint)

        for endpoint in combined.values():
            if endpoint.can_launch_tizen_app or endpoint.receiver_id:
                self._ensure_receiver_id(endpoint)
                self.media_server.receiver_url(
                    self._ensure_receiver_id(endpoint),
                    self._preferred_receiver_alias(endpoint),
                    endpoint.host,
                )
            self.tv_registry.upsert_endpoint(endpoint)

        self.endpoints = combined
        self.tv_registry.save()
        self._rebuild_endpoint_list(
            current_id=str(current_id) if current_id else None,
            selected_ids=selected_ids,
        )
        self._refresh_stage_markers()

    def _upsert_endpoint(self, combined: dict[str, TVEndpoint], incoming: TVEndpoint) -> None:
        existing = combined.get(incoming.endpoint_id)
        if existing is None and incoming.host:
            for endpoint in combined.values():
                if endpoint.host and endpoint.host == incoming.host:
                    existing = endpoint
                    break

        if existing is None:
            combined[incoming.endpoint_id] = incoming
            return
        existing.merge_from(incoming)

    def _rebuild_endpoint_list(self, current_id: str | None = None, selected_ids: set[str] | None = None) -> None:
        preserved_ids = set(selected_ids or ())
        if current_id:
            preserved_ids.add(current_id)
        blocker = QSignalBlocker(self.endpoint_list)
        self.endpoint_list.clear()
        for endpoint in sorted(self.endpoints.values(), key=lambda item: (item.source_labels()[0], item.display_name.lower())):
            name = endpoint.display_name
            if endpoint.nickname and endpoint.nickname.strip() and endpoint.nickname.strip() != endpoint.name:
                label = f"{name} ({endpoint.name})"
            else:
                label = name
            item = QListWidgetItem(f"{label}  [{' / '.join(endpoint.source_labels())}]")
            details = []
            if endpoint.host:
                details.append(endpoint.host)
            if endpoint.model_name:
                details.append(endpoint.model_name)
            details.extend(endpoint.capability_labels())
            item.setToolTip(" | ".join(details) or endpoint.source)
            item.setData(Qt.ItemDataRole.UserRole, endpoint.endpoint_id)
            self.endpoint_list.addItem(item)
            if endpoint.endpoint_id in preserved_ids:
                item.setSelected(True)
            if current_id and endpoint.endpoint_id == current_id:
                self.endpoint_list.setCurrentItem(item)
        del blocker
        preserve_stage_selection = not preserved_ids and bool(self.selected_marker_ids)
        if preserve_stage_selection:
            self._syncing_endpoint_from_stage = True
        try:
            self._endpoint_selection_changed()
        finally:
            if preserve_stage_selection:
                self._syncing_endpoint_from_stage = False

    def _endpoint_selection_changed(self) -> None:
        selected = self._selected_endpoints()
        endpoint = self._selected_endpoint()
        self._set_manage_action_state(len(selected))

        if not selected or endpoint is None:
            self.target_chip.setText("No TV selected")
            self.endpoint_details.setText("No endpoint selected.")
            self.receiver_url_field.clear()
            self.library_sync_hint.setText("Offline library: select a TV to view its sync status.")
            self.duid_field.clear()
            self.nickname_field.clear()
            self.tizen_flow_hint.setText(
                "Tizen flow: Discover TVs -> select one TV -> open the receiver app -> send the current source."
            )
            if not self._syncing_endpoint_from_stage:
                self._sync_stage_selection_from_endpoints()
            return

        if len(selected) > 1:
            names = ", ".join(item.display_name for item in selected[:4])
            remainder = len(selected) - min(len(selected), 4)
            if remainder > 0:
                names = f"{names}, +{remainder} more"
            self.target_chip.setText(f"{len(selected)} TVs selected")
            self.endpoint_details.setText(
                f"Selected TVs: {len(selected)}\n"
                f"Active TV: {endpoint.display_name}\n"
                f"Selection: {names}\n"
                "Load Selected and Chromecast unblock apply to the whole selection.\n"
                "Single-TV actions use the active TV."
            )
            receiver_url = self._receiver_state_url(endpoint)
            self.receiver_url_field.setText(receiver_url)
            self.library_sync_hint.setText("Offline library: select one TV to view its individual sync status.")
            self.duid_field.setText(endpoint.metadata.get("duid") or "")
            self.nickname_field.setText(endpoint.nickname or "")
            self.tizen_flow_hint.setText(
                "Bulk selection is active. Use the Stage And Bulk tools, or reduce to one TV for the normal Tizen receiver flow."
            )
            if not self._syncing_endpoint_from_stage:
                self._sync_stage_selection_from_endpoints()
            return

        self.target_chip.setText(f"{endpoint.display_name} | {' / '.join(endpoint.capability_labels()) or 'No playback capability'}")
        source_labels = ", ".join(endpoint.source_labels())
        capabilities = ", ".join(endpoint.capability_labels()) or "None"
        duid = endpoint.metadata.get("duid") or "n/a"
        receiver_url = self._receiver_state_url(endpoint)
        saved_control_state = self.tv_registry.control_summary(endpoint)
        self.receiver_url_field.setText(receiver_url)
        self.library_sync_hint.setText(f"Offline library: {self._library_sync_summary(endpoint)}")
        self.duid_field.setText("" if duid == "n/a" else duid)
        self.nickname_field.setText(endpoint.nickname or "")
        self.tizen_flow_hint.setText(
            f"Next for {endpoint.display_name}: open the receiver app on the TV, then send the current source through the Tizen receiver."
        )
        self.endpoint_details.setText(
            f"Recommended flow: Open Receiver App On TV -> Send Current Source To TV\n"
            f"Nickname: {endpoint.nickname or 'None'}\n"
            f"Device name: {endpoint.name}\n"
            f"Host: {endpoint.host or 'n/a'}\n"
            f"DUID: {duid}\n"
            f"Manufacturer: {endpoint.manufacturer or 'Unknown'}\n"
            f"Model: {endpoint.model_name or 'Unknown'}\n"
            f"Discovered via: {source_labels}\n"
            f"Capabilities: {capabilities}\n"
            f"Saved control state: {saved_control_state}"
        )
        if not self._syncing_endpoint_from_stage:
            self._sync_stage_selection_from_endpoints()

    def _set_manage_action_state(self, selected_count: int) -> None:
        has_selection = selected_count > 0
        is_single = selected_count == 1
        self.load_selected_tvs_button.setEnabled(has_selection)
        self.unblock_chromecast_button.setEnabled(has_selection)
        self.send_selected_button.setEnabled(is_single)
        self.open_receiver_button.setEnabled(is_single)
        self.pause_playback_button.setEnabled(is_single)
        self.copy_receiver_button.setEnabled(is_single)
        self.sync_library_button.setEnabled(has_selection)
        self.copy_duid_button.setEnabled(is_single)
        self.probe_tv_info_button.setEnabled(is_single)
        self.probe_access_button.setEnabled(is_single)
        self.nickname_field.setEnabled(is_single)
        self.save_nickname_button.setEnabled(is_single)

    def _sync_stage_selection_from_endpoints(self) -> None:
        selected_ids = {endpoint.endpoint_id for endpoint in self._selected_endpoints()}
        marker_ids = {self.endpoint_to_marker[endpoint_id] for endpoint_id in selected_ids if endpoint_id in self.endpoint_to_marker}
        active_endpoint = self._selected_endpoint()
        active_marker_id = self.endpoint_to_marker.get(active_endpoint.endpoint_id) if active_endpoint is not None else None
        self._apply_stage_selection(marker_ids, active_marker_id=active_marker_id, sync_endpoint_list=False)

    def _endpoint_double_clicked(self, item: QListWidgetItem) -> None:
        del item
        self._add_selected_endpoint_to_stage()

    def _send_remote_key(self, key: str, label: str) -> None:
        endpoint = self._selected_endpoint()
        if endpoint is None:
            QMessageBox.warning(self, "Samsung Remote", "Select a TV first.")
            return
        if not endpoint.can_use_samsung_remote:
            QMessageBox.warning(self, "Samsung Remote", "The selected TV does not have a Samsung LAN remote endpoint configured.")
            return

        self._set_status(f"Sending {label} to {endpoint.display_name}...")
        self._run_task(
            lambda: probe_samsung_remote_access(endpoint, key=key),
            on_result=lambda probe: self._handle_remote_probe(
                endpoint,
                probe,
                success_text=f"Sent {label} to {endpoint.display_name}.",
                failure_title="Samsung Remote",
            ),
            on_error=self._format_error("Samsung remote call failed"),
        )

    def _apply_remote_result(self, endpoint: TVEndpoint, token: str | None, success_text: str) -> None:
        if token:
            endpoint.samsung_remote_token = token
        self.tv_registry.upsert_endpoint(endpoint)
        self.tv_registry.record_control_state(
            endpoint,
            "samsung_remote",
            "authorized",
            "Samsung remote command succeeded.",
            port=endpoint.samsung_remote_port,
        )
        self._endpoint_selection_changed()
        self._set_status(success_text)

    def _record_remote_probe(self, endpoint: TVEndpoint, probe) -> None:
        control_name = getattr(probe, "control_name", "samsung_remote")
        if control_name == "samsung_remote":
            endpoint.samsung_remote_port = probe.port
            if probe.token:
                endpoint.samsung_remote_token = probe.token
        response_event = probe.response.get("event") if probe.response else None
        self.tv_registry.record_control_state(
            endpoint,
            control_name,
            probe.state,
            probe.detail,
            port=probe.port,
            response_event=response_event,
        )

    def _handle_remote_probe(
        self,
        endpoint: TVEndpoint,
        probe,
        *,
        success_text: str,
        failure_title: str,
    ) -> None:
        self._record_remote_probe(endpoint, probe)
        self._endpoint_selection_changed()
        if probe.ok:
            self._set_status(success_text)
            return

        self._set_status(f"{endpoint.display_name} Samsung remote state: {probe.state}.")
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setWindowTitle(failure_title)
        dialog.setText(
            f"{endpoint.display_name} did not authorize Samsung LAN control.\n\n"
            f"State: {probe.state}"
        )
        dialog.setDetailedText(probe.detail)
        dialog.exec()

    def _probe_selected_tv_access(self) -> None:
        endpoint = self._selected_endpoint()
        if endpoint is None:
            QMessageBox.warning(self, "Probe Samsung Access", "Select a TV first.")
            return
        if not endpoint.can_use_samsung_remote:
            QMessageBox.warning(self, "Probe Samsung Access", "The selected TV does not have a Samsung LAN remote endpoint configured.")
            return

        self._set_status(f"Attempting Samsung access probe for {endpoint.display_name}...")
        self._run_task(
            lambda: probe_samsung_remote_access(endpoint),
            on_result=lambda probe: self._handle_remote_probe(
                endpoint,
                probe,
                success_text=f"Samsung LAN control is authorized for {endpoint.display_name}.",
                failure_title="Probe Samsung Access",
            ),
            on_error=self._format_error("Samsung access probe failed"),
        )

    def _pause_remote_playback(self) -> None:
        endpoint = self._selected_endpoint()
        if endpoint is None:
            QMessageBox.warning(self, "Pause Playback", "Select a TV first.")
            return

        route = self._preferred_pause_route(endpoint)
        if route == "tizen_app":
            self._run_task(
                lambda: self._set_tizen_receiver_playback(endpoint, "paused"),
                on_result=lambda _: self._handle_playback_success(
                    endpoint,
                    "tizen_app",
                    "pause",
                    f"Pause command sent to {endpoint.display_name} via the Tizen receiver app.",
                ),
                on_error=self._format_error("Tizen pause failed"),
            )
            return

        if route == "chromecast":
            self._run_task(
                lambda: pause_chromecast(endpoint),
                on_result=lambda _: self._handle_playback_success(
                    endpoint,
                    "chromecast",
                    "pause",
                    f"Pause command sent to {endpoint.display_name} via Chromecast.",
                ),
                on_error=self._format_error("Chromecast pause failed"),
            )
            return

        QMessageBox.warning(self, "Pause Playback", "The selected TV does not expose a pause-capable Tizen or Chromecast route.")

    def _show_stage_remote_popup(self) -> None:
        endpoints = self._selected_stage_endpoints()
        if not endpoints:
            QMessageBox.warning(self, "Stage Remote", "Select one or more TVs on the device stage first.")
            return

        if self.stage_remote_dialog is not None:
            self.stage_remote_dialog.close()

        dialog = QDialog(self)
        dialog.setObjectName("stageRemoteDialog")
        dialog.setWindowTitle("Stage Remote")
        dialog.setWindowFlag(Qt.WindowType.Tool, True)
        dialog.setModal(False)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("Stage Remote")
        title.setObjectName("stageRemoteTitle")
        target = QLabel(self._stage_remote_target_text(endpoints))
        target.setObjectName("stageRemoteTarget")
        target.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(target)

        remote_grid = QGridLayout()
        remote_grid.setHorizontalSpacing(8)
        remote_grid.setVerticalSpacing(8)

        def remote_button(
            text: str,
            row: int,
            column: int,
            handler,
            *,
            object_name: str = "stageRemoteKeyButton",
        ) -> None:
            button = QPushButton(text)
            button.setObjectName(object_name)
            button.clicked.connect(handler)
            remote_grid.addWidget(button, row, column)

        remote_button("Home", 0, 0, lambda: self._send_remote_key_to_stage_selection("KEY_HOME", "Home"))
        remote_button("Back", 0, 2, lambda: self._send_remote_key_to_stage_selection("KEY_RETURN", "Back"))
        remote_button("Up", 1, 1, lambda: self._send_remote_key_to_stage_selection("KEY_UP", "Up"))
        remote_button("Left", 2, 0, lambda: self._send_remote_key_to_stage_selection("KEY_LEFT", "Left"))
        remote_button("OK", 2, 1, lambda: self._send_remote_key_to_stage_selection("KEY_ENTER", "Enter"), object_name="stageRemotePrimaryButton")
        remote_button("Right", 2, 2, lambda: self._send_remote_key_to_stage_selection("KEY_RIGHT", "Right"))
        remote_button("Down", 3, 1, lambda: self._send_remote_key_to_stage_selection("KEY_DOWN", "Down"))
        remote_button("Pause Playback", 4, 0, self._pause_stage_selection_playback)
        remote_button("Close", 4, 2, dialog.close)
        layout.addLayout(remote_grid)

        helper = QLabel("Targets the TVs currently selected on the device stage.")
        helper.setObjectName("stageRemoteTarget")
        helper.setWordWrap(True)
        layout.addWidget(helper)

        dialog.adjustSize()
        dialog.move(self.stage_remote_button.mapToGlobal(self.stage_remote_button.rect().bottomLeft()) + QPoint(0, 8))
        dialog.destroyed.connect(lambda *_: setattr(self, "stage_remote_dialog", None))
        dialog.show()
        self.stage_remote_dialog = dialog

    def _stage_remote_target_text(self, endpoints: list[TVEndpoint]) -> str:
        if len(endpoints) == 1:
            return f"Target: {endpoints[0].display_name}"
        return f"Targets: {len(endpoints)} TVs"

    def _send_remote_key_to_stage_selection(self, key: str, label: str) -> None:
        endpoints = self._selected_stage_endpoints()
        if not endpoints:
            QMessageBox.warning(self, "Stage Remote", "Select one or more TVs on the device stage first.")
            return
        self._send_remote_key_to_endpoints(endpoints, key=key, label=label)

    def _send_remote_key_to_endpoints(self, endpoints: list[TVEndpoint], *, key: str, label: str) -> None:
        supported = [endpoint for endpoint in endpoints if endpoint.can_use_samsung_remote]
        unsupported = [endpoint.display_name for endpoint in endpoints if not endpoint.can_use_samsung_remote]
        if not supported:
            QMessageBox.warning(self, "Stage Remote", "None of the selected TVs expose Samsung LAN remote control.")
            return

        batch = {
            "remaining": len(supported),
            "successes": [],
            "failures": [],
        }
        self._set_status(f"Sending {label} to {len(supported)} selected TV(s)...")

        def finish() -> None:
            batch["remaining"] -= 1
            if batch["remaining"] > 0:
                return
            self._endpoint_selection_changed()
            success_count = len(batch["successes"])
            failure_count = len(batch["failures"])
            skipped_count = len(unsupported)
            if failure_count:
                dialog = QMessageBox(self)
                dialog.setIcon(QMessageBox.Icon.Warning)
                dialog.setWindowTitle("Stage Remote")
                dialog.setText(f"{label} finished with {failure_count} failure(s).")
                dialog.setDetailedText("\n\n".join(batch["failures"]))
                dialog.exec()
            parts: list[str] = []
            if success_count:
                parts.append(f"sent {label} to {success_count} TV(s)")
            if failure_count:
                parts.append(f"{failure_count} failed")
            if skipped_count:
                parts.append(f"{skipped_count} skipped")
            self._set_status(f"Stage remote: {', '.join(parts)}." if parts else "Stage remote finished.")

        def on_result(endpoint: TVEndpoint, probe) -> None:
            self._record_remote_probe(endpoint, probe)
            if probe.ok:
                batch["successes"].append(endpoint.display_name)
            else:
                batch["failures"].append(f"{endpoint.display_name}\nState: {probe.state}\n{probe.detail}")
            finish()

        def on_error(endpoint: TVEndpoint, details: str) -> None:
            self.tv_registry.record_control_state(
                endpoint,
                "samsung_remote",
                "failed",
                self._error_summary(details),
                port=endpoint.samsung_remote_port,
            )
            batch["failures"].append(f"{endpoint.display_name}\n{details}")
            finish()

        for endpoint in supported:
            self._run_task(
                lambda endpoint=endpoint: probe_samsung_remote_access(endpoint, key=key),
                on_result=lambda probe, endpoint=endpoint: on_result(endpoint, probe),
                on_error=lambda details, endpoint=endpoint: on_error(endpoint, details),
            )

    def _pause_stage_selection_playback(self) -> None:
        endpoints = self._selected_stage_endpoints()
        if not endpoints:
            QMessageBox.warning(self, "Pause Playback", "Select one or more TVs on the device stage first.")
            return

        routable: list[tuple[TVEndpoint, str]] = []
        unsupported: list[str] = []
        for endpoint in endpoints:
            route = self._preferred_pause_route(endpoint)
            if route is None:
                unsupported.append(endpoint.display_name)
                continue
            routable.append((endpoint, route))
        if not routable:
            QMessageBox.warning(self, "Pause Playback", "None of the selected TVs expose a pause-capable Tizen or Chromecast route.")
            return

        batch = {
            "remaining": len(routable),
            "successes": [],
            "failures": [],
        }
        self._set_status(f"Sending pause to {len(routable)} selected TV(s)...")

        def finish() -> None:
            batch["remaining"] -= 1
            if batch["remaining"] > 0:
                return
            success_count = len(batch["successes"])
            failure_count = len(batch["failures"])
            skipped_count = len(unsupported)
            if failure_count:
                dialog = QMessageBox(self)
                dialog.setIcon(QMessageBox.Icon.Warning)
                dialog.setWindowTitle("Pause Playback")
                dialog.setText(f"Pause finished with {failure_count} failure(s).")
                dialog.setDetailedText("\n\n".join(batch["failures"]))
                dialog.exec()
            parts: list[str] = []
            if success_count:
                parts.append(f"paused {success_count} TV(s)")
            if failure_count:
                parts.append(f"{failure_count} failed")
            if skipped_count:
                parts.append(f"{skipped_count} skipped")
            self._set_status(f"Stage playback control: {', '.join(parts)}." if parts else "Pause finished.")

        def on_result(endpoint: TVEndpoint, route: str) -> None:
            self.tv_registry.record_playback_state(endpoint, route, "success", "pause")
            batch["successes"].append(endpoint.display_name)
            finish()

        def on_error(endpoint: TVEndpoint, route: str, details: str) -> None:
            self.tv_registry.record_playback_state(endpoint, route, "failed", f"pause: {self._error_summary(details)}")
            batch["failures"].append(f"{endpoint.display_name}\n{details}")
            finish()

        for endpoint, route in routable:
            if route == "tizen_app":
                self._run_task(
                    lambda endpoint=endpoint: self._set_tizen_receiver_playback(endpoint, "paused"),
                    on_result=lambda _, endpoint=endpoint, route=route: on_result(endpoint, route),
                    on_error=lambda details, endpoint=endpoint, route=route: on_error(endpoint, route, details),
                )
                continue
            self._run_task(
                lambda endpoint=endpoint: pause_chromecast(endpoint),
                on_result=lambda _, endpoint=endpoint, route=route: on_result(endpoint, route),
                on_error=lambda details, endpoint=endpoint, route=route: on_error(endpoint, route, details),
            )

    def _copy_receiver_url(self) -> None:
        endpoint = self._selected_endpoint()
        if endpoint is None:
            QMessageBox.warning(self, "Receiver URL", "Select a TV first.")
            return
        if not endpoint.can_launch_tizen_app:
            QMessageBox.warning(self, "Receiver URL", "The selected TV does not have a Tizen app route configured.")
            return
        url = self._receiver_state_url(endpoint)
        QApplication.clipboard().setText(url)
        self.receiver_url_field.setText(url)
        self._set_status(f"Copied receiver state URL for {endpoint.display_name}: {url}")

    def _copy_duid(self) -> None:
        endpoint = self._selected_endpoint()
        if endpoint is None:
            QMessageBox.warning(self, "DUID", "Select a TV first.")
            return
        duid = (endpoint.metadata.get("duid") or "").strip()
        if not duid:
            QMessageBox.warning(self, "DUID", "The selected TV does not have a DUID available.")
            return
        QApplication.clipboard().setText(duid)
        self.duid_field.setText(duid)
        self._set_status(f"Copied DUID for {endpoint.display_name}: {duid}")

    def _probe_selected_tv_info(self) -> None:
        endpoint = self._selected_endpoint()
        if endpoint is None:
            QMessageBox.warning(self, "Probe TV Info", "Select a TV first.")
            return
        if not endpoint.host:
            QMessageBox.warning(self, "Probe TV Info", "The selected TV does not have an IP address to probe.")
            return

        self._set_status(f"Probing TV info for {endpoint.display_name}...")
        self._run_task(
            lambda host=endpoint.host: probe_samsung_lan_tv(host, timeout=2.0),
            on_result=lambda probed, endpoint=endpoint: self._apply_selected_tv_info_probe(endpoint, probed),
            on_error=self._format_error("TV info probe failed"),
        )

    def _apply_selected_tv_info_probe(self, endpoint: TVEndpoint, probed: TVEndpoint | None) -> None:
        if probed is None:
            QMessageBox.warning(
                self,
                "Probe TV Info",
                f"{endpoint.display_name} did not respond to the Samsung TV info probe.",
            )
            self._set_status(f"TV info probe returned no data for {endpoint.display_name}.")
            return

        endpoint.merge_from(probed)
        for bucket in self.discovery_buckets.values():
            for candidate in bucket.values():
                if candidate is endpoint:
                    continue
                if candidate.endpoint_id == endpoint.endpoint_id or (endpoint.host and candidate.host == endpoint.host):
                    candidate.merge_from(probed)
        self.tv_registry.upsert_endpoint(endpoint)
        self.tv_registry.save()
        self._refresh_stage_markers()
        self._endpoint_selection_changed()
        duid = endpoint.metadata.get("duid") or "n/a"
        self._set_status(f"Updated TV info for {endpoint.display_name}. DUID: {duid}")

    def _open_receiver_on_tv(self) -> None:
        endpoint = self._selected_endpoint()
        if endpoint is None:
            QMessageBox.warning(self, "Open Receiver", "Select a TV first.")
            return
        if not endpoint.can_launch_tizen_app:
            QMessageBox.warning(self, "Open Tizen App", "The selected TV does not have a Tizen app route configured.")
            return

        source = self._current_source()
        if source and source.path is not None:
            served = self.media_server.publish(source.path)
            url = self._update_receiver_for_source(endpoint, source, served)
        else:
            url = self._prepare_receiver_ready(endpoint)
        QApplication.clipboard().setText(url)
        self.receiver_url_field.setText(url)

        if self._launch_receiver_app(
            endpoint,
            source_name="Receiver",
            receiver_url=url,
            failure_title="Open Receiver helper",
            show_failure_dialog=True,
        ):
            self._set_status(
                f"Copied receiver state URL for {endpoint.display_name}. Launching the Tizen receiver app..."
            )
            return

        self._set_status(
            f"Receiver state URL copied for {endpoint.display_name}: {url}."
        )

    def _open_current_source(self) -> None:
        source = self._current_source()
        if source and source.path is not None:
            subprocess.Popen(["cmd", "/c", "start", "", str(source.path)], shell=False)

    def _add_generic_marker(self) -> None:
        marker_id = self._add_stage_marker(
            endpoint_id=None,
            title=f"Marker {self.marker_counter + 1}",
            subtitle="Free stage marker",
        )
        self._handle_marker_selected(marker_id)

    def _ensure_endpoint_on_stage(self, endpoint: TVEndpoint, position: QPoint | None = None) -> str:
        existing_marker = self.endpoint_to_marker.get(endpoint.endpoint_id)
        if existing_marker:
            node = self.hub_stage.node(existing_marker)
            if node is not None and position is not None:
                node.move(position)
            return existing_marker

        subtitle = " / ".join(endpoint.capability_labels()) or "Endpoint"
        marker_id = self._add_stage_marker(
            endpoint_id=endpoint.endpoint_id,
            title=endpoint.display_name,
            subtitle=subtitle,
            position=position,
        )
        self.endpoint_to_marker[endpoint.endpoint_id] = marker_id
        return marker_id

    def _add_selected_endpoint_to_stage(self) -> None:
        endpoint = self._selected_endpoint()
        if endpoint is None:
            QMessageBox.warning(self, "Add To Stage", "Select a TV endpoint first.")
            return

        marker_id = self._ensure_endpoint_on_stage(endpoint)
        self._handle_marker_selected(marker_id)
        self.tabs.setCurrentWidget(self.stage_tab)
        self._set_status(f"Loaded {endpoint.display_name} onto the device stage.")

    def _load_selected_endpoints_to_stage(self) -> None:
        endpoints = self._selected_endpoints()
        if not endpoints:
            endpoint = self._selected_endpoint()
            endpoints = [endpoint] if endpoint is not None else []
        if not endpoints:
            QMessageBox.warning(self, "Load Selected TVs", "Select one or more TV endpoints first.")
            return

        last_marker_id: str | None = None
        for endpoint in endpoints:
            last_marker_id = self._ensure_endpoint_on_stage(endpoint)
        if last_marker_id:
            self._handle_marker_selected(last_marker_id)
        self.tabs.setCurrentWidget(self.stage_tab)
        self._set_status(f"Loaded {len(endpoints)} selected TV(s) onto the device stage.")

    def _load_all_endpoints_to_stage(self) -> None:
        endpoints = sorted(self.endpoints.values(), key=lambda endpoint: endpoint.display_name.lower())
        if not endpoints:
            QMessageBox.warning(self, "Load All TVs", "No TV endpoints are available.")
            return

        last_marker_id: str | None = None
        for endpoint in endpoints:
            last_marker_id = self._ensure_endpoint_on_stage(endpoint)
        if last_marker_id:
            self._handle_marker_selected(last_marker_id)
        self.tabs.setCurrentWidget(self.stage_tab)
        self._set_status(f"Loaded all {len(endpoints)} TV(s) onto the device stage.")

    def _prime_stage_receiver_apps(self, endpoints: list[TVEndpoint], *, context: str) -> None:
        launchable = [endpoint for endpoint in endpoints if endpoint.can_launch_tizen_app]
        if not launchable:
            return

        batch = {
            "remaining": len(launchable),
            "successes": [],
            "failures": [],
        }
        self._set_status(f"Loaded {len(endpoints)} TV(s) from {context}. Launching the Tizen receiver app where available...")

        def finish() -> None:
            batch["remaining"] -= 1
            if batch["remaining"] > 0:
                return
            success_count = len(batch["successes"])
            failure_count = len(batch["failures"])
            if failure_count:
                dialog = QMessageBox(self)
                dialog.setIcon(QMessageBox.Icon.Warning)
                dialog.setWindowTitle("Launch Tizen App")
                dialog.setText(f"Tizen app launch finished with {failure_count} failure(s).")
                dialog.setDetailedText("\n\n".join(batch["failures"]))
                dialog.exec()
            if success_count:
                self._set_status(f"Loaded {len(endpoints)} TV(s) from {context}. Tizen app launch sent to {success_count} TV(s).")
            else:
                self._set_status(f"Loaded {len(endpoints)} TV(s) from {context}. No Tizen app launch succeeded.")

        def on_result(endpoint: TVEndpoint, probe) -> None:
            self._record_remote_probe(endpoint, probe)
            self._endpoint_selection_changed()
            if probe.ok:
                self.tv_registry.record_playback_state(endpoint, "tizen_app", "success", "Receiver Ready")
                batch["successes"].append(endpoint.display_name)
            else:
                self.tv_registry.record_playback_state(endpoint, "tizen_app", "failed", f"Receiver Ready: {probe.state}: {probe.detail}")
                batch["failures"].append(f"{endpoint.display_name}\nState: {probe.state}\n{probe.detail}")
            finish()

        def on_error(endpoint: TVEndpoint, details: str) -> None:
            self.tv_registry.record_playback_state(endpoint, "tizen_app", "failed", f"Receiver Ready: {self._error_summary(details)}")
            batch["failures"].append(f"{endpoint.display_name}\n{details}")
            finish()

        for endpoint in launchable:
            self._prepare_receiver_ready(endpoint)
            self._run_task(
                lambda endpoint=endpoint: open_tizen_receiver_app(endpoint),
                on_result=lambda probe, endpoint=endpoint: on_result(endpoint, probe),
                on_error=lambda details, endpoint=endpoint: on_error(endpoint, details),
            )

    def _add_stage_marker(
        self,
        *,
        endpoint_id: str | None,
        title: str,
        subtitle: str,
        position: QPoint | None = None,
    ) -> str:
        self.marker_counter += 1
        marker_id = f"marker-{self.marker_counter}"
        if position is None:
            count = self.marker_counter - 1
            position = QPoint(36 + (count % 3) * 132, 72 + (count // 3) * 96)
        node = self.hub_stage.add_device_node(marker_id, title, subtitle, position, endpoint_id=endpoint_id)
        self.stage_marker_endpoints[marker_id] = endpoint_id
        self.marker_source_ids[marker_id] = None
        self.marker_start_offsets[marker_id] = 0.0
        node.set_start_offset(0.0)
        if endpoint_id and marker_id not in self.stage_sequence_order:
            self.stage_sequence_order.append(marker_id)
        self._refresh_stage_summary()
        if endpoint_id and self.last_source_id and self.last_source_id in self.sources:
            node.set_source(self.sources[self.last_source_id])
            self.marker_source_ids[marker_id] = self.last_source_id
        return marker_id

    def _set_marker_source(self, marker_id: str, source: SourceItem) -> None:
        node = self.hub_stage.node(marker_id)
        if node is not None:
            node.set_source(source)
            self.marker_source_ids[marker_id] = source.source_id

    def _handle_stage_sequence_delay_changed(self, value: float) -> None:
        self.stage_sequence_delay_seconds = max(0.1, float(value))
        self._refresh_stage_sequence_controls()

    def _build_stage_sequence_row(self, marker_id: str, endpoint: TVEndpoint) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)

        label = QLabel(endpoint.display_name)
        label.setMinimumWidth(0)
        label.setWordWrap(True)
        label.setProperty("markerId", marker_id)
        index_label = QLabel(f"#{self.stage_sequence_order.index(marker_id) + 1}" if marker_id in self.stage_sequence_order else "#?")
        index_label.setObjectName("mutedCopy")

        layout.addWidget(label, 1)
        layout.addWidget(index_label)
        return row

    def _handle_stage_sequence_reordered(self, *_args) -> None:
        self.stage_sequence_order = [
            str(self.stage_sequence_list.item(row).data(Qt.ItemDataRole.UserRole))
            for row in range(self.stage_sequence_list.count())
            if self.stage_sequence_list.item(row) is not None
        ]
        self._refresh_stage_sequence_controls()

    def _refresh_stage_sequence_controls(self) -> None:
        if not hasattr(self, "stage_sequence_summary"):
            return

        self._sync_stage_sequence_order()
        targets = self._sequence_targets()
        blocker = QSignalBlocker(self.stage_sequence_list.model())
        self.stage_sequence_list.clear()
        for index, (marker_id, endpoint) in enumerate(targets, start=1):
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, marker_id)
            self.stage_sequence_list.addItem(item)
            row = self._build_stage_sequence_row(marker_id, endpoint)
            item.setText(f"{index}. {endpoint.display_name}")
            item.setSizeHint(row.sizeHint())
            self.stage_sequence_list.setItemWidget(item, row)
        del blocker

        has_targets = bool(targets)
        self.stage_sequence_list.setEnabled(has_targets)
        if not has_targets:
            self.stage_sequence_summary.setText("Load TVs onto the device stage to build a playback sequence.")
            return

        self.stage_sequence_summary.setText(
            f"Each TV starts paused: TV #1 at 0.0s, TV #2 at {self._stage_sequence_delay_seconds():.1f}s, TV #3 at {self._stage_sequence_delay_seconds() * 2:.1f}s, and so on. Press Select, Enter, Pause, or Play on any staged TV remote to start every TV together."
        )

    def _refresh_stage_summary(self) -> None:
        count = len(self.stage_marker_endpoints)
        selected = len(self.selected_marker_ids)
        summary = f"{count} stage marker{'s' if count != 1 else ''} active"
        if selected:
            summary = f"{summary} | {selected} selected"
        self.device_summary.setText(summary)
        selected_endpoints = self._selected_stage_endpoints()
        self.stage_remote_button.setEnabled(bool(selected_endpoints))
        if selected_endpoints:
            self.stage_remote_button.setText(f"Remote ({len(selected_endpoints)})")
        else:
            self.stage_remote_button.setText("Remote")
        self._refresh_stage_sequence_controls()

    def _clear_marker_selection(self) -> None:
        self._apply_stage_selection(set())

    def _selected_stage_endpoints(self) -> list[TVEndpoint]:
        selected: list[TVEndpoint] = []
        seen: set[str] = set()
        for marker_id in self.selected_marker_ids:
            endpoint_id = self.stage_marker_endpoints.get(marker_id)
            if not endpoint_id or endpoint_id in seen:
                continue
            endpoint = self.endpoints.get(endpoint_id)
            if endpoint is None:
                continue
            selected.append(endpoint)
            seen.add(endpoint_id)
        return selected

    def _apply_stage_selection(
        self,
        marker_ids: set[str],
        *,
        active_marker_id: str | None = None,
        sync_endpoint_list: bool = True,
    ) -> None:
        valid_marker_ids = {marker_id for marker_id in marker_ids if marker_id in self.stage_marker_endpoints}
        if active_marker_id not in valid_marker_ids:
            active_marker_id = next(iter(valid_marker_ids), None)
        self.selected_marker_ids = valid_marker_ids
        self.selected_marker_id = active_marker_id
        self.hub_stage.set_selected_markers(valid_marker_ids)
        if not valid_marker_ids and self.stage_remote_dialog is not None:
            self.stage_remote_dialog.close()
        if hasattr(self, "remove_selected_stage_button"):
            self.remove_selected_stage_button.setVisible(bool(valid_marker_ids))
        self._refresh_stage_summary()
        if sync_endpoint_list:
            self._sync_endpoint_selection_from_stage()

    def _sync_endpoint_selection_from_stage(self) -> None:
        if not hasattr(self, "endpoint_list"):
            return
        selected_endpoint_ids = {
            endpoint_id
            for marker_id, endpoint_id in self.stage_marker_endpoints.items()
            if marker_id in self.selected_marker_ids and endpoint_id
        }
        active_endpoint_id = self.stage_marker_endpoints.get(self.selected_marker_id) if self.selected_marker_id else None
        blocker = QSignalBlocker(self.endpoint_list)
        self.endpoint_list.clearSelection()
        active_item: QListWidgetItem | None = None
        for index in range(self.endpoint_list.count()):
            item = self.endpoint_list.item(index)
            endpoint_id = item.data(Qt.ItemDataRole.UserRole)
            if endpoint_id in selected_endpoint_ids:
                item.setSelected(True)
                if endpoint_id == active_endpoint_id:
                    active_item = item
        if active_item is not None:
            self.endpoint_list.setCurrentItem(active_item)
        elif selected_endpoint_ids:
            for index in range(self.endpoint_list.count()):
                item = self.endpoint_list.item(index)
                if item.isSelected():
                    self.endpoint_list.setCurrentItem(item)
                    break
        else:
            self.endpoint_list.setCurrentRow(-1)
        del blocker
        self._syncing_endpoint_from_stage = True
        try:
            self._endpoint_selection_changed()
        finally:
            self._syncing_endpoint_from_stage = False

    def _handle_marker_selected(self, marker_id: str, additive: bool = False) -> None:
        selected = set(self.selected_marker_ids)
        if additive:
            if marker_id in selected:
                selected.remove(marker_id)
            else:
                selected.add(marker_id)
        else:
            selected = {marker_id}
        active_marker_id = marker_id if marker_id in selected else next(iter(selected), None)
        self._apply_stage_selection(selected, active_marker_id=active_marker_id)

    def _remove_selected_stage_marker(self) -> None:
        marker_ids = list(self.selected_marker_ids)
        if not marker_ids:
            QMessageBox.warning(self, "Remove Stage TV", "Select a stage marker or TV first.")
            return

        self._cancel_stage_sequence()
        for marker_id in marker_ids:
            endpoint_id = self.stage_marker_endpoints.pop(marker_id, None)
            self.marker_source_ids.pop(marker_id, None)
            self.marker_start_offsets.pop(marker_id, None)
            if marker_id in self.stage_sequence_order:
                self.stage_sequence_order.remove(marker_id)
            if endpoint_id:
                self.endpoint_to_marker.pop(endpoint_id, None)
            self.hub_stage.remove_device_node(marker_id)
        self._apply_stage_selection(set())
        self._refresh_stage_summary()
        self._set_status(f"Deleted {len(marker_ids)} selected stage marker(s).")

    def _select_endpoint_in_list(self, endpoint_id: str, *, additive: bool = False) -> None:
        blocker = QSignalBlocker(self.endpoint_list)
        if not additive:
            self.endpoint_list.clearSelection()
        for index in range(self.endpoint_list.count()):
            item = self.endpoint_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == endpoint_id:
                item.setSelected(True)
                self.endpoint_list.setCurrentItem(item)
                break
        del blocker
        self._endpoint_selection_changed()

    def _refresh_stage_markers(self) -> None:
        self._sync_stage_sequence_order()
        stale_endpoint_ids = set(self.endpoint_to_marker)
        for marker_id, endpoint_id in self.stage_marker_endpoints.items():
            node = self.hub_stage.node(marker_id)
            if node is None or endpoint_id is None:
                continue
            endpoint = self.endpoints.get(endpoint_id)
            if endpoint is None:
                node.set_endpoint(node.title_label.text(), "Endpoint unavailable", endpoint_id=None)
                self.stage_marker_endpoints[marker_id] = None
                continue
            stale_endpoint_ids.discard(endpoint_id)
            node.set_endpoint(endpoint.display_name, " / ".join(endpoint.capability_labels()) or "Endpoint", endpoint_id=endpoint.endpoint_id)
            node.set_start_offset(self._marker_start_offset(marker_id))
            self.endpoint_to_marker[endpoint.endpoint_id] = marker_id

        for endpoint_id in stale_endpoint_ids:
            self.endpoint_to_marker.pop(endpoint_id, None)
        self._refresh_stage_summary()

    def _run_task(self, func, on_result, on_error) -> None:
        worker = Worker(func)
        worker.signals.result.connect(on_result)
        worker.signals.error.connect(on_error)
        self.thread_pool.start(worker)

    def _adjust_stage_zoom(self, delta: float) -> None:
        self._set_stage_zoom(self.hub_stage.zoom + delta)

    def _set_stage_zoom(self, zoom: float) -> None:
        self.hub_stage.set_zoom(zoom)
        self._sync_stage_zoom_ui()

    def _fit_stage_zoom(self) -> None:
        if not hasattr(self, "stage_scroll"):
            return
        viewport_width = max(600, self.stage_scroll.viewport().width() - 36)
        viewport_height = max(420, self.stage_scroll.viewport().height() - 36)
        fit_zoom = min(
            viewport_width / 1160,
            viewport_height / 720,
        )
        self._set_stage_zoom(fit_zoom)

    def _sync_stage_zoom_ui(self) -> None:
        if hasattr(self, "stage_zoom_reset_button"):
            self.stage_zoom_reset_button.setText(f"{int(round(self.hub_stage.zoom * 100))}%")

    def _handle_chromecast_send_error(
        self,
        endpoint: TVEndpoint,
        source: SourceItem,
        details: str,
    ) -> None:
        summary = self._error_summary(details)
        endpoint.metadata["chromecast_playback_blocked"] = "true"
        endpoint.metadata["chromecast_playback_error"] = summary
        self.tv_registry.record_playback_state(endpoint, "chromecast", "failed", f"{source.name}: {summary}")
        self._rebuild_endpoint_list(current_id=endpoint.endpoint_id)
        self._refresh_stage_markers()
        self._endpoint_selection_changed()
        self._set_status(
            f"Chromecast failed on {endpoint.display_name}. Chromecast is blocked for this TV until you manually unblock it."
        )
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setWindowTitle("Chromecast Playback")
        dialog.setText(
            f"{summary}\n\nChromecast has been blocked for {endpoint.display_name} for the rest of this app session."
        )
        dialog.setDetailedText(details)
        dialog.exec()

    def _handle_playback_success(self, endpoint: TVEndpoint, route: str, item_name: str, success_text: str) -> None:
        if route == "chromecast":
            if "chromecast_playback_blocked" in endpoint.metadata:
                endpoint.metadata.pop("chromecast_playback_blocked", None)
                endpoint.metadata.pop("chromecast_playback_error", None)
                self.tv_registry.upsert_endpoint(endpoint)
                self._rebuild_endpoint_list(current_id=endpoint.endpoint_id)
                self._refresh_stage_markers()
                self._endpoint_selection_changed()
        self.tv_registry.record_playback_state(endpoint, route, "success", item_name)
        self._set_status(success_text)

    def _format_error(self, prefix: str):
        def handler(details: str) -> None:
            self._set_status(prefix)
            summary = self._error_summary(details)
            dialog = QMessageBox(self)
            dialog.setIcon(QMessageBox.Icon.Critical)
            dialog.setWindowTitle(prefix)
            dialog.setText(summary)
            dialog.setDetailedText(details)
            dialog.exec()

        return handler

    def _format_error_with_registry(self, endpoint: TVEndpoint, route: str, item_name: str, prefix: str):
        def handler(details: str) -> None:
            self.tv_registry.record_playback_state(endpoint, route, "failed", f"{item_name}: {self._error_summary(details)}")
            self._format_error(prefix)(details)

        return handler

    def _error_summary(self, details: str) -> str:
        lines = [line.strip() for line in details.splitlines() if line.strip()]
        for line in reversed(lines):
            if line.startswith(("RuntimeError:", "ModuleNotFoundError:", "ImportError:", "ValueError:")):
                return line
        return lines[-1] if lines else "Unknown error."

    def _set_status(self, text: str) -> None:
        self.status_label.setText(text)


def run() -> int:
    app = QApplication(sys.argv)
    font = app.font()
    font.setPointSize(10)
    app.setFont(font)

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#141414"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#ececec"))
    app.setPalette(palette)

    window = MainWindow()
    window.show()
    return app.exec()
