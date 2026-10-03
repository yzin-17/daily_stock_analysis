"""日历公开发布证据的内容绑定与历史截点回归。"""

from datetime import date, timedelta
from pathlib import Path
from shutil import copytree

import exchange_calendars as xcals
import pytest

from src.services.thesis_ledger_calendar_release import (
    RELEASE_AVAILABLE_AT, RELEASE_SOURCE_DIGEST, calendar_source_identity,
    verified_calendar_available_at,
)
from src.services.thesis_ledger_dependency_facts import calendar_fact


ROOT = Path(xcals.__file__).parent


def test_actual_package_is_bound_to_verified_release():
    assert calendar_source_identity(ROOT) == (93, RELEASE_SOURCE_DIGEST)
    assert verified_calendar_available_at("4.13.2", ROOT, RELEASE_AVAILABLE_AT) == RELEASE_AVAILABLE_AT
    assert verified_calendar_available_at("4.13.3", ROOT, RELEASE_AVAILABLE_AT) is None
    assert verified_calendar_available_at("4.13.2", ROOT, RELEASE_AVAILABLE_AT - timedelta(microseconds=1)) is None
    assert verified_calendar_available_at("4.13.2", ROOT, RELEASE_AVAILABLE_AT.replace(tzinfo=None)) is None


@pytest.mark.parametrize("change", ["missing", "extra", "modified", "symlink"])
def test_altered_package_never_inherits_release_timestamp(tmp_path, change):
    root = tmp_path / "exchange_calendars"
    copytree(ROOT, root, ignore=lambda _directory, names: [n for n in names if n == "__pycache__"])
    target = root / "exchange_calendar_xshg.py"
    if change == "missing":
        target.unlink()
    elif change == "extra":
        (root / "unknown.py").write_text("# extra")
    elif change == "modified":
        target.write_bytes(target.read_bytes() + b"\n# modified")
    else:
        target.unlink()
        target.symlink_to(ROOT / target.name)
    assert verified_calendar_available_at("4.13.2", root, RELEASE_AVAILABLE_AT) is None


def test_request_start_cannot_backdate_published_calendar():
    before = RELEASE_AVAILABLE_AT - timedelta(microseconds=1)
    assert calendar_fact(date(2020, 1, 30), date(2020, 2, 3), before) is None
    early = calendar_fact(date(2020, 1, 30), date(2020, 2, 3), RELEASE_AVAILABLE_AT)
    later = calendar_fact(date(2025, 1, 1), date(2025, 1, 3), RELEASE_AVAILABLE_AT)
    assert early is not None and later is not None
    assert early["availableAt"] == later["availableAt"] == RELEASE_AVAILABLE_AT.isoformat()
