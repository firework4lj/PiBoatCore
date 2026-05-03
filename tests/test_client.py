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
