"""真实基金净值来源核验与显式研究准入证据。"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import uuid

from data_provider.eastmoney_fund_identity import (
    IDENTITY_ENDPOINT,
    IDENTITY_REVISION,
    identity_from_raw,
    read_fund_identity,
)
from data_provider.eastmoney_fund_nav import ENDPOINT, READER_REVISION, read_fund_nav_evidence
from data_provider.eastmoney_nav_evidence import (
    canonical_json,
    hash_raw,
    validate_nav_raw_evidence,
)
from data_provider.efunds_nav_disclosure import EFUNDS_NAV_RULES
from src.services.thesis_ledger_current_data_route import current_data_adapter_revisions
from src.services.thesis_ledger_nav_calendar import build_nav_research_calendar
from src.services.thesis_ledger_nav_request import (
    NAV_ADAPTER_REVISION,
    NAV_KEY,
    NAV_SOURCE_REVISION,
    NAV_TARGET,
)
from src.services.thesis_ledger_nav_rules import (
    _instant,
    build_domestic_nav_rule,
    read_qdii_nav_rule,
)


RESEARCH_MODE = "research-assumption"
EXPECTED_REVISIONS = {
    "adapterRevision": NAV_ADAPTER_REVISION,
    "sourceRevision": NAV_SOURCE_REVISION,
    "credentialRevision": "not-required",
}
MAX_RESEARCH_DECISION_BYTES = 65536


class NavResearchAdmissionError(ValueError):
    """研究准入证据或请求不满足当前精确来源合同。"""


def _verify_capture_time(value: str, label: str) -> None:
    try:
        captured = _instant(value)
    except (ValueError, TypeError) as error:
        raise NavResearchAdmissionError(f"{label} capture time is invalid") from error
    if captured > datetime.now(timezone.utc):
        raise NavResearchAdmissionError(f"{label} capture time is in the future")


def _symbol_code(value: str) -> tuple[str, str]:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{6}(?:\.OF)?", value):
        raise NavResearchAdmissionError("symbol must be a six-digit fund code, optionally ending in .OF")
    code = value[:6]
    return code, f"{code}.OF"


def _validate_day(value: str, name: str) -> str:
    if not isinstance(value, str):
        raise NavResearchAdmissionError(f"{name} must be an ISO date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise NavResearchAdmissionError(f"{name} must be an ISO date") from error
    if parsed.isoformat() != value:
        raise NavResearchAdmissionError(f"{name} must be a canonical ISO date")
    return value


def _validate_request(
    *, symbol: str, fund_type: str, research_mode: str, start: str, end: str,
    warmup_periods: int, tail_trading_days: int, research_decision: str,
    recorded_by: str, valid_hours: int,
) -> tuple[str, str]:
    code, normalized_symbol = _symbol_code(symbol)
    if not isinstance(fund_type, str) or fund_type not in {"domestic", "qdii"}:
        raise NavResearchAdmissionError("fund_type must be explicitly domestic or qdii")
    if research_mode != RESEARCH_MODE:
        raise NavResearchAdmissionError("only research-assumption mode can be admitted")
    start = _validate_day(start, "start")
    end = _validate_day(end, "end")
    if start > end:
        raise NavResearchAdmissionError("start must not be later than end")
    if type(warmup_periods) is not int or not 1 <= warmup_periods <= 100000:
        raise NavResearchAdmissionError("warmup_periods must be between 1 and 100000")
    if type(tail_trading_days) is not int or not 1 <= tail_trading_days <= 104:
        raise NavResearchAdmissionError("tail_trading_days must be between 1 and 104")
    if (not isinstance(research_decision, str) or not research_decision.strip()
            or len(research_decision.strip().encode("utf-8")) > MAX_RESEARCH_DECISION_BYTES):
        raise NavResearchAdmissionError("research_decision must be nonempty explicit user text")
    if not isinstance(recorded_by, str) or not recorded_by.strip():
        raise NavResearchAdmissionError("recorded_by is required")
    if type(valid_hours) is not int or not 1 <= valid_hours <= 24:
        raise NavResearchAdmissionError("valid_hours must be between 1 and 24")
    return code, normalized_symbol


def _validate_funds(funds: list[dict], **request) -> list[dict[str, str]]:
    if not isinstance(funds, list) or not 1 <= len(funds) <= 16:
        raise NavResearchAdmissionError("funds must contain between 1 and 16 explicitly typed funds")
    normalized = []
    seen = set()
    for fund in funds:
        if not isinstance(fund, dict) or set(fund) != {"symbol", "fundType"}:
            raise NavResearchAdmissionError("each fund must explicitly provide symbol and fundType")
        _, symbol = _validate_request(
            symbol=fund["symbol"], fund_type=fund["fundType"], **request,
        )
        if symbol in seen:
            raise NavResearchAdmissionError(f"duplicate fund symbol: {symbol}")
        seen.add(symbol)
        normalized.append({"symbol": symbol, "fundType": fund["fundType"]})
    return normalized


def _current_revisions() -> dict[str, str]:
    revisions = current_data_adapter_revisions(NAV_KEY, NAV_TARGET)
    if revisions != EXPECTED_REVISIONS:
        raise NavResearchAdmissionError("current NAV adapter, source, or credential revision is unsupported")
    return revisions


def _identity(code: str, fund_type: str, reader) -> object:
    evidence = reader(code)
    if (getattr(evidence, "fund_code", None) != code
            or not isinstance(getattr(evidence, "response_raw", None), str)
            or not isinstance(getattr(evidence, "captured_at", None), str)):
        raise NavResearchAdmissionError("fund identity source evidence is missing or malformed")
    try:
        checked = identity_from_raw(evidence.response_raw, code, evidence.captured_at)
    except (ValueError, TypeError, KeyError) as error:
        raise NavResearchAdmissionError("fund identity raw response failed verification") from error
    if evidence != checked:
        raise NavResearchAdmissionError("fund identity object differs from its verified raw response")
    _verify_capture_time(checked.captured_at, "fund identity")
    if checked.fund_type != fund_type:
        raise NavResearchAdmissionError(
            f"explicit fund_type {fund_type!r} does not match current source identity {checked.fund_type!r}"
        )
    return checked


def _source(code: str, reader):
    source = reader(code)
    try:
        validate_nav_raw_evidence(source)
    except (ValueError, TypeError, AttributeError) as error:
        raise NavResearchAdmissionError("NAV raw response evidence is missing or invalid") from error
    if (source.fund_code != code or source.endpoint != ENDPOINT
            or source.reader_revision != READER_REVISION):
        raise NavResearchAdmissionError("NAV raw response does not match the current EastMoney reader")
    _verify_capture_time(source.captured_at, "NAV raw source")
    return source


def _rule_evidence(
    *, symbol: str, fund_type: str, start: str, end: str, warmup_periods: int,
    source, decision_raw: str, cutoff: str, qdii_reader,
):
    if fund_type == "domestic":
        earliest = min(record.valuation_date for record in source.records)
        rule_decision_raw = canonical_json({
            "schemaVersion": "nav-research-default-v1",
            "symbol": symbol,
            "fundType": "domestic",
            "delayWorkdays": 1,
            "applicableRange": {"startDate": earliest, "endDate": end},
            "configuredAt": json.loads(decision_raw)["configuredAt"],
            "decision": json.loads(decision_raw)["decision"],
        })
        evidence = build_domestic_nav_rule(
            rule_decision_raw,
            evidence_ref=f"research-config://nav-domestic/{hash_raw(rule_decision_raw)}",
            data_as_of=cutoff,
        )
        _verify_capture_time(evidence.captured_at, "domestic rule")
        return evidence, rule_decision_raw

    spec = EFUNDS_NAV_RULES.get(symbol)
    if spec is None:
        raise NavResearchAdmissionError("QDII NAV rule is not audited for this fund")
    preceding = sorted(
        record.valuation_date for record in source.records
        if spec.audited_start <= record.valuation_date < start
    )
    if len(preceding) < warmup_periods:
        raise NavResearchAdmissionError("source NAV dates do not provide the requested QDII warmup")
    evidence = qdii_reader(symbol, start=preceding[-warmup_periods], end=end, data_as_of=cutoff)
    _verify_capture_time(evidence.captured_at, "QDII rule")
    if (evidence.rule.get("symbol") != symbol or evidence.rule.get("fundType") != "qdii"
            or evidence.rule.get("applicableRange") != {
            "startDate": preceding[-warmup_periods], "endDate": end,
    }):
        raise NavResearchAdmissionError("verified QDII rule does not match the requested fund and range")
    return evidence, None


def _private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or not path.is_dir():
        raise NavResearchAdmissionError("evidence-dir must be a real directory")
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise NavResearchAdmissionError("evidence-dir must not be accessible by group or other users")


def _write_private(path: Path, content: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(descriptor)


def _persist_evidence(base: Path, artifacts: dict[str, tuple[bytes, str]], manifest: dict) -> dict:
    temporary = Path(tempfile.mkdtemp(prefix=".nav-research-", dir=base))
    os.chmod(temporary, 0o700)
    try:
        artifact_manifest = {}
        for name, (content, media_type) in artifacts.items():
            if Path(name).name != name:
                raise NavResearchAdmissionError("invalid evidence artifact name")
            _write_private(temporary / name, content)
            artifact_manifest[name] = {
                "sha256": hashlib.sha256(content).hexdigest(),
                "bytes": len(content),
                "mediaType": media_type,
            }
        manifest["artifacts"] = artifact_manifest
        manifest_bytes = canonical_json(manifest).encode("utf-8")
        _write_private(temporary / "manifest.json", manifest_bytes)
        first_symbol = manifest["funds"][0]["symbol"]
        run_name = f"nav-research-{first_symbol[:6]}-{uuid.uuid4().hex}"
        final = base / run_name
        os.rename(temporary, final)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    digest = hashlib.sha256(manifest_bytes).hexdigest()
    return {
        "directory": str(final),
        "manifestPath": str(final / "manifest.json"),
        "evidenceRef": f"nav-research-evidence://{digest}",
        "evidenceSha256": digest,
    }


def prepare_nav_research_evidence(
    *, funds: list[dict], research_mode: str, start: str, end: str,
    warmup_periods: int, tail_trading_days: int, research_decision: str,
    evidence_dir: str | Path, recorded_by: str, valid_hours: int,
    identity_reader=None, nav_reader=None, qdii_reader=None,
) -> dict:
    """Verify a batch completely, then persist one evidence package for one Store row."""
    common_request = {
        "research_mode": research_mode,
        "start": start,
        "end": end,
        "warmup_periods": warmup_periods,
        "tail_trading_days": tail_trading_days,
        "research_decision": research_decision,
        "recorded_by": recorded_by,
        "valid_hours": valid_hours,
    }
    normalized_funds = _validate_funds(funds, **common_request)
    revisions = _current_revisions()
    output_dir = Path(evidence_dir).expanduser()
    _private_directory(output_dir)

    decision_text = research_decision.strip()
    configured_at = datetime.now(timezone.utc).isoformat()
    captured_funds = []
    artifacts: dict[str, tuple[bytes, str]] = {}
    for fund in normalized_funds:
        symbol = fund["symbol"]
        fund_type = fund["fundType"]
        code = symbol[:6]
        decision_raw = canonical_json({
            "schemaVersion": "nav-research-calendar-decision-v1",
            "symbol": symbol,
            "basis": "nav-dates-xshg-intersection-v1",
            "configuredAt": configured_at,
            "decision": decision_text,
        })
        identity = _identity(code, fund_type, identity_reader or read_fund_identity)
        source = _source(code, nav_reader or read_fund_nav_evidence)
        # The production verifiers require a cutoff after their local/source captures.
        # It is used only to validate this research evidence window, never as a PIT claim.
        rule_cutoff = (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat()
        rule, domestic_rule_decision_raw = _rule_evidence(
            symbol=symbol, fund_type=fund_type, start=start, end=end,
            warmup_periods=warmup_periods, source=source, decision_raw=decision_raw,
            cutoff=rule_cutoff, qdii_reader=qdii_reader or read_qdii_nav_rule,
        )
        calendar_cutoff = (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat()
        try:
            calendar = build_nav_research_calendar(
                source, rule, decision_raw, mode=research_mode, start=start, end=end,
                warmup_periods=warmup_periods, tail_trading_days=tail_trading_days,
                data_as_of=calendar_cutoff,
            )
        except (ValueError, TypeError, KeyError, IndexError) as error:
            raise NavResearchAdmissionError(
                f"{symbol} NAV dates do not cover the requested warmup, execution and tail"
            ) from error

        coverage = calendar.calendar.get("coverage")
        if (not isinstance(coverage, dict) or coverage.get("complete") is not True
                or not isinstance(coverage.get("startDate"), str)
                or not isinstance(coverage.get("endDate"), str)):
            raise NavResearchAdmissionError(f"{symbol} research calendar lacks bounded source coverage")
        source_dates = sorted(record.valuation_date for record in source.records)
        prewarm_dates = [day for day in source_dates if day < start][-warmup_periods:]
        execution_dates = [day for day in source_dates if start <= day <= end]
        tail_dates = [
            day for day in calendar.calendar.get("tradingDates", []) if day > end
        ][:tail_trading_days]
        if (len(prewarm_dates) != warmup_periods or not execution_dates
                or len(tail_dates) != tail_trading_days
                or coverage["startDate"] != prewarm_dates[0]
                or coverage["endDate"] < tail_dates[-1]):
            raise NavResearchAdmissionError(
                f"{symbol} evidence does not cover the requested warmup, execution and tail"
            )

        prefix = code
        identity_file = f"{prefix}-fund-identity.raw"
        rule_file = f"{prefix}-rule.raw"
        rule_json_file = f"{prefix}-rule.json"
        calendar_file = f"{prefix}-calendar.json"
        assumption_file = f"{prefix}-calendar-assumption.json"
        decision_file = f"{prefix}-calendar-decision.json"
        artifacts[identity_file] = (
            identity.response_raw.encode("utf-8"), "application/json; charset=utf-8",
        )
        artifacts[rule_file] = (
            rule.document_raw,
            "application/pdf" if rule.document_kind == "fund-prospectus" else "application/json; charset=utf-8",
        )
        artifacts[rule_json_file] = (rule.rule_raw.encode("utf-8"), "application/json; charset=utf-8")
        artifacts[calendar_file] = (calendar.calendar_raw.encode("utf-8"), "application/json; charset=utf-8")
        artifacts[assumption_file] = (calendar.assumption_raw.encode("utf-8"), "application/json; charset=utf-8")
        artifacts[decision_file] = (calendar.decision_raw.encode("utf-8"), "application/json; charset=utf-8")
        domestic_decision_file = None
        if domestic_rule_decision_raw is not None:
            domestic_decision_file = f"{prefix}-domestic-rule-decision.json"
            artifacts[domestic_decision_file] = (
                domestic_rule_decision_raw.encode("utf-8"), "application/json; charset=utf-8",
            )
        page_files = []
        for page in source.pages:
            name = f"{prefix}-nav-page-{page.page_index:03d}.raw.json"
            artifacts[name] = (page.raw_response.encode("utf-8"), "application/json; charset=utf-8")
            page_files.append({"pageIndex": page.page_index, "file": name, "sha256": page.content_hash})

        captured_funds.append({
            "symbol": symbol,
            "fundType": fund_type,
            "identity": identity,
            "source": source,
            "sourceDates": source_dates,
            "rule": rule,
            "calendar": calendar,
            "coverage": coverage,
            "prewarmDates": prewarm_dates,
            "executionDates": execution_dates,
            "tailDates": tail_dates,
            "files": {
                "identity": identity_file,
                "rule": rule_file,
                "ruleJson": rule_json_file,
                "calendar": calendar_file,
                "assumption": assumption_file,
                "decision": decision_file,
                "domesticRuleDecision": domestic_decision_file,
                "pages": page_files,
            },
        })

    scope_date_from = min(item["coverage"]["startDate"] for item in captured_funds)
    scope_date_to = max(item["coverage"]["endDate"] for item in captured_funds)
    for item in captured_funds:
        if item["sourceDates"][0] > scope_date_from or item["sourceDates"][-1] < scope_date_to:
            raise NavResearchAdmissionError(
                f"{item['symbol']} raw NAV dates do not cover the common batch admission range"
            )

    manifest = {
        "schemaVersion": "nav-research-admission-evidence-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "researchMode": research_mode,
        "researchDecision": decision_text,
        "researchDecisionConfiguredAt": configured_at,
        "requestedRange": {"start": start, "end": end},
        "requiredCoverage": {"startDate": scope_date_from, "endDate": scope_date_to},
        "funds": [
            {
                "symbol": item["symbol"],
                "fundType": item["fundType"],
                "requiredCoverage": {
                    "startDate": item["coverage"]["startDate"],
                    "endDate": item["coverage"]["endDate"],
                    "warmupPeriods": warmup_periods,
                    "warmupDates": item["prewarmDates"],
                    "executionDates": item["executionDates"],
                    "tailTradingDays": tail_trading_days,
                    "tailDates": item["tailDates"],
                },
                "fundIdentity": {
                    "fundCode": item["identity"].fund_code,
                    "sourceType": item["identity"].source_type,
                    "fundType": item["identity"].fund_type,
                    "endpoint": IDENTITY_ENDPOINT,
                    "readerRevision": IDENTITY_REVISION,
                    "capturedAt": item["identity"].captured_at,
                    "rawFile": item["files"]["identity"],
                    "rawSha256": hash_raw(item["identity"].response_raw),
                },
                "navSource": {
                    "endpoint": item["source"].endpoint,
                    "readerRevision": item["source"].reader_revision,
                    "capturedAt": item["source"].captured_at,
                    "totalCount": item["source"].total_count,
                    "contentHash": item["source"].content_hash,
                    "firstValuationDate": item["sourceDates"][0],
                    "lastValuationDate": item["sourceDates"][-1],
                    "pageFiles": item["files"]["pages"],
                },
                "rule": {
                    "documentKind": item["rule"].document_kind,
                    "capturedAt": item["rule"].captured_at,
                    "readerRevision": item["rule"].reader_revision,
                    "contentHash": item["rule"].rule["contentHash"],
                    "ruleFile": item["files"]["ruleJson"],
                    "documentFile": item["files"]["rule"],
                },
                "calendar": {
                    "contentHash": hash_raw(item["calendar"].calendar_raw),
                    "assumptionHash": hash_raw(item["calendar"].assumption_raw),
                    "decisionHash": hash_raw(item["calendar"].decision_raw),
                },
            }
            for item in captured_funds
        ],
        "route": {
            "key": NAV_KEY,
            "target": NAV_TARGET,
            **revisions,
        },
        "limitations": [
            "仅适用于 research-assumption 研究假设模式",
            "不授予 strict-publication 或严格历史发布时间资格",
            "当前基金身份不能证明历史基金分类",
            "来源净值日期不能证明暂停交易或申购限制完整性",
        ],
    }
    persisted = _persist_evidence(output_dir, artifacts, manifest)
    return {
        "funds": [
            {"symbol": item["symbol"], "fundType": item["fundType"]}
            for item in captured_funds
        ],
        "scopeSymbols": [item["symbol"] for item in captured_funds],
        "researchMode": research_mode,
        "scopeDateFrom": scope_date_from,
        "scopeDateTo": scope_date_to,
        "adapterRevision": revisions["adapterRevision"],
        "sourceRevision": revisions["sourceRevision"],
        "credentialRevision": revisions["credentialRevision"],
        **persisted,
    }


def _verify_persisted_evidence(evidence: dict) -> None:
    manifest_path = Path(evidence["manifestPath"])
    evidence_dir = manifest_path.parent
    if (evidence_dir.is_symlink() or not evidence_dir.is_dir()
            or stat.S_IMODE(evidence_dir.stat().st_mode) & 0o077):
        raise NavResearchAdmissionError("evidence directory permissions are not private")
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise NavResearchAdmissionError("evidence manifest is missing")
    if stat.S_IMODE(manifest_path.stat().st_mode) & 0o077:
        raise NavResearchAdmissionError("evidence manifest permissions are not private")
    manifest_bytes = manifest_path.read_bytes()
    digest = hashlib.sha256(manifest_bytes).hexdigest()
    if digest != evidence.get("evidenceSha256") or evidence.get("evidenceRef") != f"nav-research-evidence://{digest}":
        raise NavResearchAdmissionError("evidence manifest hash does not match the prepared admission")
    try:
        manifest = json.loads(manifest_bytes)
    except (ValueError, TypeError) as error:
        raise NavResearchAdmissionError("evidence manifest is invalid JSON") from error
    if not isinstance(manifest, dict):
        raise NavResearchAdmissionError("evidence manifest must be an object")
    manifest_funds = manifest.get("funds")
    expected_funds = evidence.get("funds")
    if (not isinstance(manifest_funds, list) or not isinstance(expected_funds, list)
            or sorted((item.get("symbol"), item.get("fundType")) for item in manifest_funds)
            != sorted((item.get("symbol"), item.get("fundType")) for item in expected_funds)
            or manifest.get("researchMode") != RESEARCH_MODE
            or manifest.get("requiredCoverage", {}).get("startDate") != evidence.get("scopeDateFrom")
            or manifest.get("requiredCoverage", {}).get("endDate") != evidence.get("scopeDateTo")
            or manifest.get("route", {}).get("key") != NAV_KEY
            or manifest.get("route", {}).get("target") != NAV_TARGET
            or manifest.get("route", {}).get("adapterRevision") != EXPECTED_REVISIONS["adapterRevision"]
            or manifest.get("route", {}).get("sourceRevision") != EXPECTED_REVISIONS["sourceRevision"]
            or manifest.get("route", {}).get("credentialRevision") != "not-required"):
        raise NavResearchAdmissionError("evidence manifest scope or current revisions changed")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        raise NavResearchAdmissionError("evidence manifest contains no source artifacts")
    for name, entry in artifacts.items():
        if not isinstance(name, str) or Path(name).name != name or not isinstance(entry, dict):
            raise NavResearchAdmissionError("evidence artifact reference is invalid")
        path = manifest_path.parent / name
        if path.is_symlink() or not path.is_file() or stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise NavResearchAdmissionError("a private source evidence artifact is missing")
        if hashlib.sha256(path.read_bytes()).hexdigest() != entry.get("sha256"):
            raise NavResearchAdmissionError("a source evidence artifact hash does not match its manifest")


def record_nav_research_admission(
    store, evidence: dict, *, recorded_by: str, valid_hours: int,
) -> dict:
    """Write one explicitly requested, batch-scoped admission after evidence recheck."""
    if type(valid_hours) is not int or not 1 <= valid_hours <= 24:
        raise NavResearchAdmissionError("valid_hours must be between 1 and 24")
    if not isinstance(recorded_by, str) or not recorded_by.strip():
        raise NavResearchAdmissionError("recorded_by is required")
    revisions = _current_revisions()
    if any(evidence.get(key) != value for key, value in revisions.items()):
        raise NavResearchAdmissionError("prepared evidence no longer matches current NAV revisions")
    _verify_persisted_evidence(evidence)
    symbols = evidence.get("scopeSymbols")
    if (not isinstance(symbols, list) or not symbols
            or sorted(item.get("symbol") for item in evidence.get("funds", [])) != sorted(symbols)):
        raise NavResearchAdmissionError("prepared fund scope is invalid")
    providers = store.provider_registry()
    efinance = [item for item in providers if item.get("providerId") == "efinance"]
    if (len(efinance) != 1 or efinance[0].get("configured") is not True
            or efinance[0].get("enabled") is not True
            or efinance[0].get("requiresCredential") is not False
            or efinance[0].get("tombstone") is not None):
        raise NavResearchAdmissionError("current efinance provider state does not match the NAV route")
    existing = store.get_route_admission_v3(key=NAV_KEY, target=NAV_TARGET)
    if existing is not None:
        existing_symbols = existing.get("scopeSymbols")
        if (not isinstance(existing_symbols, list)
                or any(item not in symbols for item in existing_symbols)):
            raise NavResearchAdmissionError(
                "current admission contains a fund outside this verified batch; it will not be dropped or merged"
            )
    valid_from = datetime.now(timezone.utc)
    valid_until = valid_from + timedelta(hours=valid_hours)
    return store.record_route_admission_v3(
        key=NAV_KEY,
        target=NAV_TARGET,
        evidence_ref=evidence["evidenceRef"],
        evidence_sha256=evidence["evidenceSha256"],
        scope_symbols=symbols,
        scope_date_from=evidence["scopeDateFrom"],
        scope_date_to=evidence["scopeDateTo"],
        adapter_revision=revisions["adapterRevision"],
        source_revision=revisions["sourceRevision"],
        credential_revision=revisions["credentialRevision"],
        valid_from=valid_from.isoformat(),
        valid_until=valid_until.isoformat(),
        recorded_by=recorded_by.strip(),
    )
