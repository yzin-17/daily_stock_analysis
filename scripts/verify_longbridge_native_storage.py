"""在专用 SDK 构建环境运行；发行版 SDK 不满足本验收前提。"""

import asyncio
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from longbridge.openapi import OAuthBuilder


class NativeOAuthStorageTests(unittest.TestCase):
    def setUp(self):
        self.assertEqual(getattr(OAuthBuilder, "THESIS_LEDGER_STORAGE_VERSION", None), 1)
        self.snapshot = json.dumps({
            "client_id": "native-storage-test",
            "access_token": "fixture-access-not-a-real-token",
            "refresh_token": "fixture-refresh-not-a-real-token",
            "expires_at": int(time.time()) + 3600,
        })

    def test_valid_snapshot_uses_no_file_or_authorization(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict("os.environ", {"HOME": directory, "XDG_CONFIG_HOME": directory}):
                events = []
                client = OAuthBuilder(
                    "native-storage-test", token_json=self.snapshot,
                    on_token_save=lambda value: events.append("save"),
                    allow_authorization=False,
                ).build(lambda url: events.append("open"))
                self.assertIsNotNone(client)
                self.assertEqual(events, [])
                self.assertEqual(list(Path(directory).rglob("*")), [])

    def test_unattended_missing_token_fails_without_opening_browser(self):
        events = []
        with self.assertRaises(Exception) as caught:
            OAuthBuilder(
                "native-storage-test", on_token_save=lambda value: None,
                allow_authorization=False,
            ).build(events.append)
        self.assertIn("authorization", str(caught.exception).lower())
        self.assertEqual(events, [])

    def test_invalid_snapshot_is_rejected_without_echoing_secrets(self):
        with self.assertRaises(ValueError) as caught:
            OAuthBuilder("another-client", token_json=self.snapshot, on_token_save=lambda value: None)
        self.assertNotIn("fixture-access", str(caught.exception))
        with self.assertRaises(ValueError):
            OAuthBuilder("native-storage-test", token_json=self.snapshot)
        with self.assertRaises(ValueError):
            OAuthBuilder("native-storage-test", on_token_save="not-callable")

    def test_async_snapshot_and_unattended_failure(self):
        async def run():
            events = []
            client = await OAuthBuilder(
                "native-storage-test", token_json=self.snapshot,
                on_token_save=lambda value: None, allow_authorization=False,
            ).build_async(events.append)
            self.assertIsNotNone(client)
            with self.assertRaises(Exception):
                await OAuthBuilder(
                    "native-storage-test", on_token_save=lambda value: None,
                    allow_authorization=False,
                ).build_async(events.append)
            self.assertEqual(events, [])
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
