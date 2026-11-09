"""按市场拥有刷新范围；发布仍使用原有完整快照与租约事务。"""

from functools import partial
import os

from data_provider.futu_catalog import load_futu_catalog
from data_provider.futu_market import FutuMarketError
from src.services import thesis_ledger_catalog as catalog
from src.services.thesis_ledger_catalog import CatalogBuildError
from src.services.thesis_ledger_catalog_contract import current_catalog_row
from src.services.thesis_ledger_catalog_execution import catalog_checkpoint

SCOPE_MARKETS = {"CN": {"SH", "SZ", "BJ", "OF"}, "HK_US": {"HK", "US"}}


def _read_futu(host, port):
    try:
        return load_futu_catalog(host, port)
    except FutuMarketError as exc:
        raise CatalogBuildError(str(exc), code=exc.code, retryable=False) from None


def merge_catalog_scope(previous, incoming, scope):
    markets = SCOPE_MARKETS[scope]
    if not incoming or any(item["market"] not in markets for item in incoming):
        raise CatalogBuildError("目录响应超出本次市场范围", code="catalog_provider_invalid_response", retryable=False)
    items = [item for item in previous if item["market"] not in markets] + incoming
    return sorted(items, key=lambda item: (item["canonicalCode"], item["market"], item["instrumentType"]))


def _futu_catalog(store):
    provider = next((p for p in store.provider_registry() if p["providerId"] == "futu"), None)
    if not provider or not provider["enabled"] or not provider["configured"]:
        raise CatalogBuildError("富途来源未启用或账户未配置", code="catalog_provider_not_configured", retryable=False)
    credentials = store.provider_credential_snapshot("futu")
    return catalog._bounded_call(partial(_read_futu, credentials.values.get("host"), credentials.values.get("port")),
                                 timeout_seconds=60, max_attempts=1)


def build_scoped_catalog(store, scope):
    if scope not in SCOPE_MARKETS:
        raise CatalogBuildError("目录市场范围无效", code="catalog_invalid_scope", retryable=False)
    with store._connect() as connection:
        row = connection.execute(
            "SELECT * FROM thesis_ledger_catalog_generation ORDER BY generation DESC LIMIT 1"
        ).fetchone()
        previous = current_catalog_row(row)["items"] if row else []
    fixture = os.getenv("THESIS_LEDGER_FIXTURE_MODE", "false").strip().lower() in {"1", "true", "yes", "on"}
    if fixture:
        from src.services.thesis_ledger_control import DEFAULT_CATALOG
        incoming, failures = [item for item in DEFAULT_CATALOG if item["market"] in SCOPE_MARKETS[scope]], {}
    elif scope == "HK_US":
        incoming, failures = _futu_catalog(store), {}
    else:
        incoming, failures = catalog.build_catalog()
    return merge_catalog_scope(previous, incoming, scope), failures


def execute_catalog_job(store, job_id, owner):
    started = store._start_catalog_job(job_id, owner)
    if started is None:
        from src.services.thesis_ledger_control import ControlContractError
        raise ControlContractError("CATALOG_JOB_NOT_FOUND", "Catalog Job 不存在，无法执行", status_code=409)
    if started["status"] != "running" or started["owner"] != owner:
        return store._job_payload(started)
    try:
        items, failures = build_scoped_catalog(store, started["scope"])
        catalog_checkpoint()
        return store._job_payload(store._complete_catalog_job(job_id, owner, items, failures))
    except Exception as exc:
        failure = {"code": str(getattr(exc, "code", "catalog_provider_unavailable")),
                   "retryable": bool(getattr(exc, "retryable", True))}
        if hasattr(exc, "code"):
            failure["message"] = str(exc)
        failed = store._fail_catalog_job(job_id, owner, error=failure)
        if failed is None:
            raise
        return store._job_payload(failed)
