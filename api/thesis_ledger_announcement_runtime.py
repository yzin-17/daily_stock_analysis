"""公告获取服务装配；复用已有生效路由和数据源启用配置。"""
from functools import partial
from data_provider.cninfo_announcements import acquire_announcements
from src.services.thesis_ledger_announcements import AnnouncementRepository
from src.services.thesis_ledger_announcement_jobs import AnnouncementJobs
from src.services.thesis_ledger_announcement_runtime import AnnouncementRuntime
from src.services.thesis_ledger_control import ThesisLedgerControlStore


def announcements_selected(store):
    policy = store.effective_policy_v3()
    if not policy or not policy.get("enabled"):
        return False
    if not any(p["providerId"] == "official-states" and p["enabled"] for p in store.provider_registry()):
        return False
    return any(route.get("key", {}).get("market") == "CN"
               and route.get("key", {}).get("assetType") == "STOCK"
               and route.get("key", {}).get("capability") == "HISTORICAL_TRADING_STATUS"
               and any(t.get("providerId") == "official-states" and t.get("eligible")
                       and t.get("upstreamSource") in ("cn-stock-daily-suspension", "cn-stock-risk-price-limits")
                       for t in route.get("targets", [])) for route in policy.get("routes", []))


def create_announcement_runtime():
    repository = AnnouncementRepository()
    return AnnouncementRuntime(AnnouncementJobs(repository.root),
                               partial(acquire_announcements, repository=repository),
                               partial(announcements_selected, ThesisLedgerControlStore()))
