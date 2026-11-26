"""公告获取服务装配；复用已有生效路由和数据源启用配置。"""
from functools import partial
from data_provider.cninfo_announcements import acquire_announcements
from data_provider.thesis_ledger_sources.official_states import RISK, SUSPENSION
from src.services.thesis_ledger_announcements import AnnouncementRepository
from src.services.thesis_ledger_announcement_jobs import AnnouncementJobs
from src.services.thesis_ledger_announcement_runtime import AnnouncementRuntime
from src.services.thesis_ledger_control import ThesisLedgerControlStore

ANNOUNCEMENT_TARGETS = {
    ("DAILY_TRADING_STATUS", SUSPENSION),
    ("RISK_WARNING", RISK),
    ("DAILY_PRICE_RULE", RISK),
}


def announcements_selected(store):
    policy = store.effective_policy_v3()
    if not policy or not policy.get("enabled"):
        return False
    if not any(p["providerId"] == "official-states" and p["enabled"] for p in store.provider_registry()):
        return False
    return any(route.get("key", {}).get("market") == "CN"
               and route.get("key", {}).get("assetType") == "STOCK"
               and route.get("enabled") is not False
               and any(t.get("providerId") == "official-states" and t.get("eligible")
                       and (route.get("key", {}).get("capability"), t.get("upstreamSource")) in ANNOUNCEMENT_TARGETS
                       for t in route.get("targets", [])) for route in policy.get("routes", []))


def create_announcement_runtime():
    repository = AnnouncementRepository()
    return AnnouncementRuntime(AnnouncementJobs(repository.root),
                               partial(acquire_announcements, repository=repository),
                               partial(announcements_selected, ThesisLedgerControlStore()))
