import json
import tempfile
import unittest
from pathlib import Path
from urllib.request import Request, urlopen

from multihub.media_server import DEFAULT_MEDIA_SERVER_PORT, MediaHTTPServer, OfflineLibrarySource


class MediaServerTests(unittest.TestCase):
    def test_hub_url_matches_base_url(self):
        server = MediaHTTPServer(port=0)
        self.addCleanup(server._server.server_close)

        self.assertEqual(server.base_url, server.hub_url)

    def test_receiver_url_uses_short_preferred_alias(self):
        server = MediaHTTPServer(port=0)
        self.addCleanup(server._server.server_close)
        url = server.receiver_url("receiver-host-10-0-0-5", "5")

        self.assertTrue(url.endswith("/r/5"))

    def test_receiver_state_alias_url_uses_short_preferred_alias(self):
        server = MediaHTTPServer(port=0)
        self.addCleanup(server._server.server_close)
        url = server.receiver_state_alias_url("receiver-host-10-0-0-5", "5")

        self.assertTrue(url.endswith("/receiver-state-alias/5"))

    def test_receiver_url_resolves_alias_collisions(self):
        server = MediaHTTPServer(port=0)
        self.addCleanup(server._server.server_close)
        first = server.receiver_url("receiver-one", "tv")
        second = server.receiver_url("receiver-two", "tv")

        self.assertTrue(first.endswith("/r/tv"))
        self.assertNotEqual(first, second)
        self.assertIn("/r/", second)

    def test_default_port_matches_receiver_app_expectation(self):
        self.assertEqual(65331, DEFAULT_MEDIA_SERVER_PORT)

    def test_receiver_host_mapping_overrides_shared_alias(self):
        server = MediaHTTPServer(port=0)
        self.addCleanup(server._server.server_close)

        server.update_receiver(
            "receiver-one",
            source_name="One",
            mime_type="text/plain",
            media_url=None,
            note="one",
            preferred_alias="tv",
            preferred_host="10.0.0.1",
        )
        server.update_receiver(
            "receiver-two",
            source_name="Two",
            mime_type="text/plain",
            media_url=None,
            note="two",
            preferred_alias="tv",
            preferred_host="10.0.0.2",
        )

        self.assertEqual("receiver-one", server._receivers.resolve_request("tv", "10.0.0.1"))
        self.assertEqual("receiver-two", server._receivers.resolve_request("tv", "10.0.0.2"))

    def test_media_endpoint_supports_byte_ranges(self):
        server = MediaHTTPServer(port=0)
        self.addCleanup(server.stop)
        with tempfile.NamedTemporaryFile(delete=False) as temp_file:
            temp_file.write(b"abcdef")
            temp_path = Path(temp_file.name)
        self.addCleanup(lambda: temp_path.unlink(missing_ok=True))

        served = server.publish(temp_path)
        server.start()

        request = Request(served.url, headers={"Range": "bytes=1-3"})
        with urlopen(request) as response:
            payload = response.read()
            content_range = response.headers.get("Content-Range")
            accept_ranges = response.headers.get("Accept-Ranges")
            status = response.status

        self.assertEqual(206, status)
        self.assertEqual(b"bcd", payload)
        self.assertEqual("bytes 1-3/6", content_range)
        self.assertEqual("bytes", accept_ranges)

    def test_receiver_library_manifest_and_status_are_bound_to_tv_host(self):
        server = MediaHTTPServer(port=0)
        self.addCleanup(server.stop)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as temp_file:
            temp_file.write(b"offline-library")
            temp_path = Path(temp_file.name)
        self.addCleanup(lambda: temp_path.unlink(missing_ok=True))

        source = OfflineLibrarySource(
            item_id="sample.png",
            name="sample.png",
            mime_type="image/png",
            size=temp_path.stat().st_size,
            content_hash="a" * 64,
            path=temp_path,
            playable=True,
        )
        revision = server.set_offline_library([source])
        server.update_receiver(
            "receiver-one",
            source_name="Ready",
            mime_type="text/plain",
            media_url=None,
            note="ready",
            preferred_host="127.0.0.1",
        )
        server.request_offline_library_sync(["receiver-one"])
        server.start()
        base_url = f"http://127.0.0.1:{server.port}"

        with urlopen(f"{base_url}/receiver-library-current") as response:
            manifest = json.loads(response.read().decode("utf-8"))

        self.assertEqual(revision, manifest["revision"])
        self.assertEqual("sample.png", manifest["entries"][0]["id"])
        self.assertTrue(manifest["request_id"])
        self.assertIn("/media/", manifest["entries"][0]["media_url"])

        payload = json.dumps(
            {
                "request_id": manifest["request_id"],
                "revision": manifest["revision"],
                "state": "synced",
                "detail": "Saved one source.",
                "stored_bytes": source.size,
            }
        ).encode("utf-8")
        request = Request(
            f"{base_url}/receiver-library-status",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request) as response:
            self.assertEqual(200, response.status)

        status = server.receiver_library_status("receiver-one")
        self.assertIsNotNone(status)
        self.assertEqual("synced", status.state)
        self.assertEqual(source.size, status.stored_bytes)

    def test_library_revision_changes_when_source_content_fingerprint_changes(self):
        server = MediaHTTPServer(port=0)
        self.addCleanup(server._server.server_close)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as temp_file:
            temp_file.write(b"source")
            temp_path = Path(temp_file.name)
        self.addCleanup(lambda: temp_path.unlink(missing_ok=True))

        first = OfflineLibrarySource("sample.png", "sample.png", "image/png", 6, "a" * 64, temp_path, True)
        second = OfflineLibrarySource("sample.png", "sample.png", "image/png", 6, "b" * 64, temp_path, True)
        self.assertNotEqual(server.set_offline_library([first]), server.set_offline_library([second]))


if __name__ == "__main__":
    unittest.main()
