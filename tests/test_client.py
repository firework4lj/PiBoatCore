import unittest
from unittest.mock import patch

from pi_boat_core.client import TelemetryClient


class TelemetryClientTests(unittest.TestCase):
    def test_post_heartbeat_adds_bearer_token_when_configured(self) -> None:
        captured = {}

        class Response:
            status = 202

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

        def fake_urlopen(request, timeout):
            captured["authorization"] = request.headers.get("Authorization")
            captured["timeout"] = timeout
            return Response()

        client = TelemetryClient(
            server_url="http://example.test/api/heartbeat",
            timeout_seconds=5,
            api_token="secret-token",
        )

        with patch("urllib.request.urlopen", fake_urlopen):
            client.post_heartbeat({"t": "1,boat,device,1,2026-05-03T00:00:00Z,ok"})

        self.assertEqual(captured["authorization"], "Bearer secret-token")
        self.assertEqual(captured["timeout"], 5)

    def test_post_snapshot_posts_jpeg_to_snapshot_endpoint(self) -> None:
        captured = {}

        class Response:
            status = 202

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["content_type"] = request.headers.get("Content-type")
            captured["boat_id"] = request.headers.get("X-boat-id")
            captured["authorization"] = request.headers.get("Authorization")
            captured["body"] = request.data
            return Response()

        client = TelemetryClient(
            server_url="http://example.test/api/heartbeat",
            timeout_seconds=5,
            api_token="secret-token",
        )

        with patch("urllib.request.urlopen", fake_urlopen):
            client.post_snapshot(
                boat_id="boat",
                device_id="pi",
                sent_at="2026-05-03T00:00:00Z",
                image=b"jpeg",
            )

        self.assertEqual(captured["url"], "http://example.test/api/snapshot")
        self.assertEqual(captured["content_type"], "image/jpeg")
        self.assertEqual(captured["boat_id"], "boat")
        self.assertEqual(captured["authorization"], "Bearer secret-token")
        self.assertEqual(captured["body"], b"jpeg")
