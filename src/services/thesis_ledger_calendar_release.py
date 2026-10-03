"""已核验日历发布制品的离线身份与可用时间，不由请求日期推断。"""

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path


RELEASE_VERSION = "4.13.2"
RELEASE_AVAILABLE_AT = datetime(2026, 3, 10, 3, 24, 37, 55242, tzinfo=timezone.utc)
RELEASE_SOURCE_DIGEST = "3dd6286cd2404bbe188e843a7ada5625164fff6059eb18c69d080213c3e29dea"
RELEASE_FILE_COUNT = 93
RELEASE_ADAPTER_REVISION = "release-evidence-v1"


def calendar_source_identity(package_root: Path) -> tuple[int, str]:
    """摘要算法绑定 wheel 内 package 相对路径和每份源码的原字节。"""
    if package_root.is_symlink() or not package_root.is_dir():
        raise ValueError("日历包目录无效")
    files = sorted(package_root.rglob("*.py"))
    if len(files) != RELEASE_FILE_COUNT:
        raise ValueError("日历包文件集合不匹配")
    records = []
    total_bytes = 0
    for path in files:
        if path.is_symlink() or not path.is_file():
            raise ValueError("日历包源码类型无效")
        content = path.read_bytes()
        total_bytes += len(content)
        if total_bytes > 16 * 1024 * 1024:
            raise ValueError("日历包源码超出核验预算")
        relative = path.relative_to(package_root).as_posix()
        records.append(f"exchange_calendars/{relative}:{sha256(content).hexdigest()}")
    return len(records), sha256("\n".join(records).encode()).hexdigest()


def verified_calendar_available_at(
    version: str, package_root: Path, data_as_of: datetime,
) -> datetime | None:
    if version != RELEASE_VERSION or data_as_of.tzinfo is None:
        return None
    if data_as_of < RELEASE_AVAILABLE_AT:
        return None
    try:
        count, digest = calendar_source_identity(package_root)
    except (OSError, ValueError):
        return None
    if count != RELEASE_FILE_COUNT or digest != RELEASE_SOURCE_DIGEST:
        return None
    return RELEASE_AVAILABLE_AT
