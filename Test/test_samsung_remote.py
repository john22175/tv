import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from multihub import connectors
from multihub.models import TVEndpoint
from multihub.tv_registry import TVStateRegistry


class FakeSocket:
    def __init__(self, token="token-123", messages=None):
        self._messages = [
            json.dumps({"event": "ms.channel.connect", "data": {"token": token}}),
        ]
        if messages:
            self._messages.extend(messages)
        self.sent_messages = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def recv(self, timeout=None):
        del timeout
        if self._messages:
            return self._messages.pop(0)
        raise TimeoutError("no more fake websocket messages")

    def send(self, payload):
        self.sent_messages.append(json.loads(payload))


class SamsungRemoteTests(unittest.TestCase):
    def test_tizen_receiver_launch_uses_one_samsung_remote_session_when_available(self):
        endpoint = TVEndpoint(
            endpoint_id="host-10-0-0-8",
            name="TV",
            host="10.0.0.8",
            samsung_remote_port=8001,
        )
        original_open_receiver = connectors.open_samsung_receiver_app
        original_resolve = connectors._resolve_tizen_cli
        original_run = connectors._run_tizen_cli

        def fake_open_receiver(candidate):
            self.assertIs(candidate, endpoint)
            return connectors.SamsungRemoteProbe(
                ok=True,
                port=8001,
                state="authorized",
                detail="MultiHub Receiver app launch command was sent.",
                token="approved-token",
                control_name="tizen_app_launch",
            )

        def fail_resolve(tool_name):
            raise AssertionError(f"SDB should not be used when Samsung LAN launch succeeds: {tool_name}")

        def fail_run(args, *, timeout):
            del timeout
            raise AssertionError(f"SDB should not be used when Samsung LAN launch succeeds: {args}")

        connectors.open_samsung_receiver_app = fake_open_receiver
        connectors._resolve_tizen_cli = fail_resolve
        connectors._run_tizen_cli = fail_run
        try:
            probe = connectors.open_tizen_receiver_app(endpoint)
        finally:
            connectors.open_samsung_receiver_app = original_open_receiver
            connectors._resolve_tizen_cli = original_resolve
            connectors._run_tizen_cli = original_run

        self.assertTrue(probe.ok)
        self.assertEqual("authorized", probe.state)
        self.assertEqual("tizen_app_launch", probe.control_name)
        self.assertEqual("approved-token", endpoint.samsung_remote_token)

    def test_tizen_receiver_launch_uses_dev_channel(self):
        endpoint = TVEndpoint(
            endpoint_id="host-10-0-0-3",
            name="TV",
            host="10.0.0.3",
        )
        original_resolve = connectors._resolve_tizen_cli
        original_run = connectors._run_tizen_cli
        seen_commands = []

        def fake_resolve(tool_name):
            return tool_name

        def fake_run(args, *, timeout):
            del timeout
            seen_commands.append(args)
            if args == ["sdb", "connect", "10.0.0.3"]:
                return subprocess.CompletedProcess(
                    args=args,
                    returncode=0,
                    stdout="connected to 10.0.0.3:26101",
                    stderr="",
                )
            if args == ["sdb", "devices"]:
                return subprocess.CompletedProcess(
                    args=args,
                    returncode=0,
                    stdout="10.0.0.3:26101\tdevice",
                    stderr="",
                )
            if args[:4] == ["sdb", "-s", "10.0.0.3:26101", "shell"]:
                return subprocess.CompletedProcess(
                    args=args,
                    returncode=0,
                    stdout="'MultiHub Receiver'\t 'MHubRcvr01.MultiHubReceiver'",
                    stderr="",
                )
            if args == ["tz", "run", "-p", "MHubRcvr01", "-e", "10.0.0.3:26101"]:
                return subprocess.CompletedProcess(
                    args=args,
                    returncode=0,
                    stdout="... successfully launched pid = 1042 with debug 0",
                    stderr="",
                )
            raise AssertionError(f"Unexpected command: {args}")

        connectors._resolve_tizen_cli = fake_resolve
        connectors._run_tizen_cli = fake_run
        try:
            probe = connectors.open_tizen_receiver_app(endpoint)
        finally:
            connectors._resolve_tizen_cli = original_resolve
            connectors._run_tizen_cli = original_run

        self.assertTrue(probe.ok)
        self.assertEqual("launched", probe.state)
        self.assertEqual("tizen_app_launch", probe.control_name)
        self.assertIn("successfully launched", probe.detail)
        self.assertEqual(
            [
                ["sdb", "connect", "10.0.0.3"],
                ["sdb", "devices"],
                ["sdb", "-s", "10.0.0.3:26101", "shell", "0", "applist"],
                ["tz", "run", "-p", "MHubRcvr01", "-e", "10.0.0.3:26101"],
            ],
            seen_commands,
        )

    def test_tizen_receiver_launch_requires_installed_package(self):
        endpoint = TVEndpoint(
            endpoint_id="host-10-0-0-4",
            name="TV",
            host="10.0.0.4",
        )
        original_resolve = connectors._resolve_tizen_cli
        original_run = connectors._run_tizen_cli

        def fake_resolve(tool_name):
            return tool_name

        def fake_run(args, *, timeout):
            del timeout
            if args == ["sdb", "connect", "10.0.0.4"]:
                return subprocess.CompletedProcess(
                    args=args,
                    returncode=0,
                    stdout="connected to 10.0.0.4:26101",
                    stderr="",
                )
            if args == ["sdb", "devices"]:
                return subprocess.CompletedProcess(
                    args=args,
                    returncode=0,
                    stdout="10.0.0.4:26101\tdevice",
                    stderr="",
                )
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout="'Internet'\t 'org.tizen.browser'",
                stderr="",
            )

        connectors._resolve_tizen_cli = fake_resolve
        connectors._run_tizen_cli = fake_run
        try:
            probe = connectors.open_tizen_receiver_app(endpoint)
        finally:
            connectors._resolve_tizen_cli = original_resolve
            connectors._run_tizen_cli = original_run

        self.assertFalse(probe.ok)
        self.assertEqual("not_installed", probe.state)
        self.assertEqual("tizen_app_launch", probe.control_name)

    def test_tizen_receiver_launch_recovers_serial_from_devices_listing(self):
        endpoint = TVEndpoint(
            endpoint_id="host-10-0-0-5",
            name="TV",
            host="10.0.0.5",
            metadata={"duid": "uuid:sample-duid-123"},
        )
        original_resolve = connectors._resolve_tizen_cli
        original_run = connectors._run_tizen_cli
        seen_commands = []

        def fake_resolve(tool_name):
            return tool_name

        def fake_run(args, *, timeout):
            del timeout
            seen_commands.append(args)
            if args == ["sdb", "connect", "10.0.0.5"]:
                return subprocess.CompletedProcess(
                    args=args,
                    returncode=0,
                    stdout="connected to 10.0.0.5:26101",
                    stderr="",
                )
            if args == ["sdb", "devices"]:
                return subprocess.CompletedProcess(
                    args=args,
                    returncode=0,
                    stdout="sample-duid-123\tdevice",
                    stderr="",
                )
            if args[:4] == ["sdb", "-s", "sample-duid-123", "shell"]:
                return subprocess.CompletedProcess(
                    args=args,
                    returncode=0,
                    stdout="'MultiHub Receiver'\t 'MHubRcvr01.MultiHubReceiver'",
                    stderr="",
                )
            if args == ["tz", "run", "-p", "MHubRcvr01", "-e", "sample-duid-123"]:
                return subprocess.CompletedProcess(
                    args=args,
                    returncode=0,
                    stdout="... successfully launched pid = 1042 with debug 0",
                    stderr="",
                )
            raise AssertionError(f"Unexpected command: {args}")

        connectors._resolve_tizen_cli = fake_resolve
        connectors._run_tizen_cli = fake_run
        try:
            probe = connectors.open_tizen_receiver_app(endpoint)
        finally:
            connectors._resolve_tizen_cli = original_resolve
            connectors._run_tizen_cli = original_run

        self.assertTrue(probe.ok)
        self.assertEqual("launched", probe.state)
        self.assertEqual(
            [
                ["sdb", "connect", "10.0.0.5"],
                ["sdb", "devices"],
                ["sdb", "-s", "sample-duid-123", "shell", "0", "applist"],
                ["tz", "run", "-p", "MHubRcvr01", "-e", "sample-duid-123"],
            ],
            seen_commands,
        )

    def test_samsung_remote_retries_without_stale_token(self):
        endpoint = TVEndpoint(
            endpoint_id="host-10-0-0-10",
            name="TV",
            host="10.0.0.10",
            samsung_remote_port=8002,
            samsung_remote_token="stale-token",
            metadata={"available_ports": "8002,8001"},
        )
        seen_uris = []
        original_connect = connectors.ws_connect

        class HandshakeSocket:
            def __init__(self, initial_payload):
                self.initial_payload = initial_payload

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def recv(self, timeout=None):
                del timeout
                return json.dumps(self.initial_payload)

            def send(self, payload):
                del payload

        def fake_connect(uri, ssl_context=None, open_timeout=None, close_timeout=None):
            del ssl_context, open_timeout, close_timeout
            seen_uris.append(uri)
            if "token=stale-token" in uri:
                return HandshakeSocket(
                    {"event": "ms.channel.unauthorized", "data": {"token": "stale-token"}}
                )
            self.assertNotIn("token=", uri)
            return HandshakeSocket(
                {"event": "ms.channel.connect", "data": {"token": "fresh-token"}}
            )

        connectors.ws_connect = fake_connect
        try:
            probe = connectors.probe_samsung_remote_access(endpoint)
        finally:
            connectors.ws_connect = original_connect

        self.assertTrue(probe.ok)
        self.assertEqual("authorized", probe.state)
        self.assertEqual("fresh-token", probe.token)
        self.assertEqual(
            [
                "wss://10.0.0.10:8002/api/v2/channels/samsung.remote.control?name=UHlRdCBNdWx0aUh1Yg%3D%3D&token=stale-token",
                "wss://10.0.0.10:8002/api/v2/channels/samsung.remote.control?name=UHlRdCBNdWx0aUh1Yg%3D%3D",
            ],
            seen_uris,
        )
        self.assertIsNone(endpoint.samsung_remote_token)

    def test_samsung_remote_retries_once_after_unauthorized_prompt(self):
        endpoint = TVEndpoint(
            endpoint_id="host-10-0-0-11",
            name="TV",
            host="10.0.0.11",
            samsung_remote_port=8002,
            metadata={"available_ports": "8002,8001"},
        )
        original_connect = connectors.ws_connect
        original_sleep = connectors.time.sleep
        seen_uris = []
        connect_count = {"count": 0}

        class HandshakeSocket:
            def __init__(self, initial_payload):
                self.initial_payload = initial_payload

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def recv(self, timeout=None):
                del timeout
                return json.dumps(self.initial_payload)

            def send(self, payload):
                del payload

        def fake_connect(uri, ssl_context=None, open_timeout=None, close_timeout=None):
            del ssl_context, open_timeout, close_timeout
            seen_uris.append(uri)
            connect_count["count"] += 1
            if connect_count["count"] == 1:
                return HandshakeSocket({"event": "ms.channel.unauthorized", "data": {}})
            return HandshakeSocket({"event": "ms.channel.connect", "data": {"token": "approved-token"}})

        connectors.ws_connect = fake_connect
        connectors.time.sleep = lambda seconds: None
        try:
            probe = connectors.probe_samsung_remote_access(endpoint)
        finally:
            connectors.ws_connect = original_connect
            connectors.time.sleep = original_sleep

        self.assertTrue(probe.ok)
        self.assertEqual("authorized", probe.state)
        self.assertEqual("approved-token", probe.token)
        self.assertEqual(2, connect_count["count"])
        self.assertEqual(
            [
                "wss://10.0.0.11:8002/api/v2/channels/samsung.remote.control?name=UHlRdCBNdWx0aUh1Yg%3D%3D",
                "wss://10.0.0.11:8002/api/v2/channels/samsung.remote.control?name=UHlRdCBNdWx0aUh1Yg%3D%3D",
            ],
            seen_uris,
        )

    def test_samsung_probe_captures_duid_metadata(self):
        original_get = connectors.requests.get
        seen_urls = []

        def fake_get(url, timeout=None, verify=None):
            del timeout, verify
            seen_urls.append(url)
            self.assertIn(url, {"https://10.0.0.9:8002/api/v2/", "http://10.0.0.9:8001/api/v2/"})

            class Response:
                def raise_for_status(self):
                    return None

                def json(self):
                    return {
                        "device": {
                            "name": "Living Room TV",
                            "manufacturer": "Samsung",
                            "modelName": "QN65Q7FAAFXZA",
                            "deviceType": "TV",
                            "OS": "Tizen",
                            "duid": "uuid:sample-duid-123",
                        },
                        "isSupport": {},
                    }

            return Response()

        connectors.requests.get = fake_get
        try:
            endpoint = connectors.probe_samsung_lan_tv("10.0.0.9", timeout=1.0)
        finally:
            connectors.requests.get = original_get

        self.assertIsNotNone(endpoint)
        assert endpoint is not None
        self.assertEqual("uuid:sample-duid-123", endpoint.metadata["duid"])
        self.assertTrue(seen_urls)

    def test_endpoint_merge_replaces_blank_duid_with_probed_value(self):
        endpoint = TVEndpoint(
            endpoint_id="host-10-0-0-9",
            name="TV",
            host="10.0.0.9",
            metadata={"duid": ""},
        )
        probed = TVEndpoint(
            endpoint_id="host-10-0-0-9",
            name="TV",
            host="10.0.0.9",
            metadata={"duid": "uuid:sample-duid-123"},
        )

        endpoint.merge_from(probed)

        self.assertEqual("uuid:sample-duid-123", endpoint.metadata["duid"])

    def test_browser_launch_falls_back_to_secondary_port(self):
        endpoint = TVEndpoint(
            endpoint_id="host-10-0-0-1",
            name="TV",
            host="10.0.0.1",
            samsung_remote_port=8002,
            metadata={"available_ports": "8002,8001"},
        )
        sockets = {}
        original_connect = connectors.ws_connect
        original_post = connectors.requests.post

        def fake_connect(uri, ssl_context=None, open_timeout=None, close_timeout=None):
            del ssl_context, open_timeout, close_timeout
            if ":8002/" in uri:
                raise TimeoutError("primary port timed out")
            socket = FakeSocket(
                messages=[
                    json.dumps(
                        {
                            "event": "ed.installedApp.get",
                            "data": {
                                "data": [
                                    {"appId": "11101200001", "name": "YouTube", "app_type": 2},
                                    {"appId": "3202010022079", "name": "Internet", "app_type": 2},
                                ]
                            },
                        }
                    )
                ]
            )
            sockets["socket"] = socket
            return socket

        def fake_post(url, timeout=None, verify=None):
            del timeout, verify
            self.assertTrue(
                url.endswith("/api/v2/applications/3202010022079")
                or url.endswith("/api/v2/applications/org.tizen.browser")
            )

            class Response:
                ok = True
                status_code = 200

            return Response()

        connectors.ws_connect = fake_connect
        connectors.requests.post = fake_post
        try:
            probe = connectors.open_samsung_browser_url(endpoint, "http://example.test/receiver")
        finally:
            connectors.ws_connect = original_connect
            connectors.requests.post = original_post

        self.assertTrue(probe.ok)
        self.assertEqual(8001, probe.port)
        self.assertEqual(8001, endpoint.samsung_remote_port)
        self.assertEqual("token-123", probe.token)
        self.assertEqual("ed.installedApp.get", sockets["socket"].sent_messages[0]["params"]["event"])
        self.assertEqual("ed.apps.launch", sockets["socket"].sent_messages[1]["params"]["event"])
        self.assertEqual("3202010022079", sockets["socket"].sent_messages[1]["params"]["data"]["appId"])
        self.assertEqual("DEEP_LINK", sockets["socket"].sent_messages[1]["params"]["data"]["action_type"])
        self.assertEqual(
            "http://example.test/receiver",
            sockets["socket"].sent_messages[1]["params"]["data"]["metaTag"],
        )
        self.assertEqual("org.tizen.browser", sockets["socket"].sent_messages[2]["params"]["data"]["appId"])
        self.assertEqual("NATIVE_LAUNCH", sockets["socket"].sent_messages[2]["params"]["data"]["action_type"])
        self.assertEqual("DEEP_LINK", sockets["socket"].sent_messages[3]["params"]["data"]["action_type"])
        self.assertEqual("3202010022079", endpoint.metadata["browser_app_id"])
        self.assertEqual("DEEP_LINK", endpoint.metadata["browser_launch_type"])
        self.assertEqual(4, len(sockets["socket"].sent_messages))

    def test_receiver_target_prefers_installed_multihub_app(self):
        endpoint = TVEndpoint(
            endpoint_id="host-10-0-0-3",
            name="TV",
            host="10.0.0.3",
            samsung_remote_port=8002,
            metadata={"available_ports": "8002,8001"},
        )
        sockets = {}
        original_connect = connectors.ws_connect
        original_post = connectors.requests.post

        def fake_connect(uri, ssl_context=None, open_timeout=None, close_timeout=None):
            del ssl_context, open_timeout, close_timeout, uri
            socket = FakeSocket(
                messages=[
                    json.dumps(
                        {
                            "event": "ed.installedApp.get",
                            "data": {
                                "data": [
                                    {"appId": "MHubRcvr01.MultiHubReceiver", "name": "MultiHub Receiver", "app_type": 4},
                                    {"appId": "3202010022079", "name": "Internet", "app_type": 2},
                                ]
                            },
                        }
                    )
                ]
            )
            sockets["socket"] = socket
            return socket

        def fake_post(url, timeout=None, verify=None):
            del timeout, verify
            self.assertTrue(url.endswith("/api/v2/applications/MHubRcvr01.MultiHubReceiver"))

            class Response:
                ok = True
                status_code = 200

            return Response()

        connectors.ws_connect = fake_connect
        connectors.requests.post = fake_post
        try:
            probe = connectors.open_samsung_receiver_target(endpoint, "http://example.test/r/5")
        finally:
            connectors.ws_connect = original_connect
            connectors.requests.post = original_post

        self.assertTrue(probe.ok)
        self.assertEqual("MultiHub Receiver app launch command was sent.", probe.detail)
        self.assertEqual("ed.installedApp.get", sockets["socket"].sent_messages[0]["params"]["event"])
        self.assertEqual("MHubRcvr01.MultiHubReceiver", sockets["socket"].sent_messages[1]["params"]["data"]["appId"])
        self.assertEqual("NATIVE_LAUNCH", sockets["socket"].sent_messages[1]["params"]["data"]["action_type"])
        self.assertEqual("MHubRcvr01.MultiHubReceiver", endpoint.metadata["receiver_app_id"])
        self.assertEqual("NATIVE_LAUNCH", endpoint.metadata["receiver_launch_type"])
        self.assertEqual(2, len(sockets["socket"].sent_messages))

    def test_registry_persists_samsung_remote_token(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            registry_path = Path(temp_dir) / "tv_registry.json"
            registry = TVStateRegistry(registry_path)
            endpoint = TVEndpoint(
                endpoint_id="host-10-0-0-2",
                name="TV",
                host="10.0.0.2",
                samsung_remote_port=8001,
                samsung_remote_token="saved-token",
            )

            registry.upsert_endpoint(endpoint)
            registry.save()

            reloaded = TVStateRegistry(registry_path)
            endpoints = reloaded.list_endpoints()

        self.assertEqual(1, len(endpoints))
        self.assertEqual("saved-token", endpoints[0].samsung_remote_token)


if __name__ == "__main__":
    unittest.main()
