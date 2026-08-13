import unittest
from unittest.mock import patch

from multihub.connectors import (
    CHROMECAST_QUEUE_LOAD,
    CHROMECAST_REPEAT_SINGLE,
    _build_chromecast_looping_video_request,
    play_to_chromecast,
)
from multihub.models import TVEndpoint


class ChromecastLoopRequestTests(unittest.TestCase):
    def test_video_queue_load_repeats_single_item(self) -> None:
        request = _build_chromecast_looping_video_request(
            "http://127.0.0.1:9000/media/demo.mp4",
            "video/mp4",
            "Demo Clip",
        )

        self.assertEqual(request["type"], CHROMECAST_QUEUE_LOAD)
        self.assertEqual(request["repeatMode"], CHROMECAST_REPEAT_SINGLE)
        self.assertEqual(request["startIndex"], 0)
        self.assertEqual(request["currentTime"], 0)
        self.assertTrue(request["autoplay"])

        item = request["items"][0]
        self.assertTrue(item["autoplay"])
        self.assertEqual(item["startTime"], 0)
        self.assertEqual(item["preloadTime"], 0)
        self.assertEqual(item["media"]["contentId"], "http://127.0.0.1:9000/media/demo.mp4")
        self.assertEqual(item["media"]["contentType"], "video/mp4")
        self.assertEqual(item["media"]["streamType"], "BUFFERED")
        self.assertEqual(item["media"]["metadata"]["title"], "Demo Clip")
        self.assertEqual(item["media"]["metadata"]["metadataType"], 0)

    def test_play_to_chromecast_falls_back_to_known_host_lookup(self) -> None:
        class FakeStatus:
            media_session_id = 1
            content_id = "http://127.0.0.1/media/demo.gif"
            player_state = "IDLE"

        class FakeMediaController:
            def __init__(self) -> None:
                self.status = FakeStatus()
                self.play_calls = []

            def play_media(self, *args, **kwargs) -> None:
                self.play_calls.append((args, kwargs))

            def block_until_active(self, timeout=None) -> None:
                del timeout

            def update_status(self) -> None:
                return

        class FakeCast:
            def __init__(self, host: str, uuid_text: str) -> None:
                self.cast_info = type("CastInfo", (), {"host": host, "uuid": uuid_text})()
                self.media_controller = FakeMediaController()

            def wait(self, timeout=None) -> None:
                del timeout

            def disconnect(self, timeout=2) -> None:
                del timeout

        class FakeDiscovery:
            @staticmethod
            def stop_discovery(browser) -> None:
                del browser

        class FakePyChromecastModule:
            def __init__(self) -> None:
                self.fallback_cast = FakeCast("10.0.0.5", "11111111-1111-1111-1111-111111111111")

            def get_listed_chromecasts(self, **kwargs):
                del kwargs
                return ([], "listed-browser")

            def get_chromecasts(self, **kwargs):
                del kwargs
                return ([self.fallback_cast], "fallback-browser")

        endpoint = TVEndpoint(
            endpoint_id="host-10-0-0-5",
            name="TV",
            host="10.0.0.5",
            chromecast_uuid="11111111-1111-1111-1111-111111111111",
            chromecast_port=8009,
        )
        fake_module = FakePyChromecastModule()

        with patch("multihub.connectors._require_pychromecast", return_value=(fake_module, FakeDiscovery)), patch(
            "multihub.connectors.time.sleep",
            return_value=None,
        ):
            play_to_chromecast(endpoint, "http://127.0.0.1/media/demo.gif", "image/gif", "Demo Gif")

        self.assertEqual(1, len(fake_module.fallback_cast.media_controller.play_calls))


if __name__ == "__main__":
    unittest.main()
