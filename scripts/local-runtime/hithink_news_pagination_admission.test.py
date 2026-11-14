"""分页准入升级保持范围、有效期与真实两页来源证据。"""

from datetime import datetime, timedelta, timezone
import json
import sys
from types import SimpleNamespace, ModuleType
import unittest
from unittest.mock import patch

import hithink_news_pagination_admission as worker


def record():
    now = datetime.now(timezone.utc)
    return {"status": "admitted", "invalidated_at": None, "invalidation_reason": None,
            "consumer": "thesis-ledger", "route_key_json": json.dumps(worker.KEY),
            "provider_id": "hithink", "upstream_source": worker.TARGET["upstreamSource"],
            "scope_symbols_json": '["600036.SH"]', "scope_date_from": "2026-11-03", "scope_date_to": "2026-11-13",
            "valid_from": (now - timedelta(hours=1)).isoformat(), "valid_until": (now + timedelta(hours=1)).isoformat(),
            "adapter_revision": worker.OLD[0], "source_revision": worker.OLD[1], "credential_revision": "controlled-cred",
            "record_version": 6, "recorded_by": "old-operator", "recorded_at": now.isoformat(),
            "evidence_ref": "sha256:old", "evidence_sha256": "old"}


class PaginationAdmissionTests(unittest.TestCase):
    def test_old_revision_identity_credentials_and_time_are_required(self):
        row = record()
        worker.validate_previous(row, datetime.now(timezone.utc), "controlled-cred")
        for changes in [{"scope_symbols_json": '["600519.SH"]'}, {"adapter_revision": "other"},
                        {"credential_revision": "different"}, {"status": "revoked"},
                        {"valid_until": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()}]:
            with self.assertRaises(AssertionError):
                worker.validate_previous({**row, **changes}, datetime.now(timezone.utc), "controlled-cred")

    def test_only_revisions_and_evidence_metadata_can_change(self):
        row = record()
        upgraded = {**row, "adapter_revision": worker.NEW[0], "source_revision": worker.NEW[1], "record_version": 7}
        worker.verify_unchanged(row, upgraded)
        for key in ["scope_symbols_json", "scope_date_from", "scope_date_to", "valid_from", "valid_until", "credential_revision"]:
            with self.assertRaises(AssertionError):
                worker.verify_unchanged(row, {**upgraded, key: "changed"})

    def test_probe_uses_real_second_offset_and_requires_distinct_pages(self):
        calls = []
        def read(_settings, symbol, observer, _deadline, **kwargs):
            calls.append((symbol, kwargs))
            observer.send(("attempt", None))
            observer.send(("http_status", 200))
            index = len(calls)
            return SimpleNamespace(records=[SimpleNamespace(source_item_id=str(index))],
                                   has_more=index == 1, offset="literal-offset" if index == 1 else None,
                                   response_sha256=str(index) * 64)
        module = ModuleType("data_provider.hithink_news")
        module.fetch_hithink_news = read
        with patch.dict(sys.modules, {"data_provider.hithink_news": module}):
            proof = worker.capture(object())
        self.assertEqual(proof["upstreamAttempts"], 2)
        self.assertEqual(calls[1][1], {"max_results": 5, "offset": "literal-offset"})


if __name__ == "__main__":
    unittest.main()
