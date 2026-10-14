"""历史日线的日期坐标及实际市场收市校验；CN既有表示保持不变。"""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from src.services.thesis_ledger_multimarket_calendar import multimarket_calendar_fact


class MarketDailyPointSource:
    def __init__(self, request, fetched_at, legacy_timestamp, legacy_point):
        self.market = request["routeKey"]["market"]
        self.fetched_at = fetched_at
        self.observed_at = datetime.fromisoformat(fetched_at)
        self.legacy_timestamp, self.legacy_point = legacy_timestamp, legacy_point
        self.timezone = "Asia/Shanghai"
        self.calendar = None
        if self.market != "CN":
            self.calendar = multimarket_calendar_fact(self.market,
                date.fromisoformat(request["start"]), date.fromisoformat(request["end"]), self.observed_at)
            if self.calendar is None:
                raise ValueError("历史日线缺少核验后的市场会话")
            self.timezone = self.calendar["timezone"]

    def timestamp(self, source_date):
        if self.market == "CN":
            return self.legacy_timestamp(source_date)
        # 精确日线适配器已将供应商日期规范为当地交易日；不把UTC零点误映射到美股前一天。
        day = date.fromisoformat(str(source_date))
        return datetime.combine(day, datetime.min.time(), ZoneInfo(self.timezone)).isoformat()

    def point(self, item):
        if self.market == "CN":
            return self.legacy_point(item, fetched_at=self.fetched_at, observed_availability=True)
        day = datetime.fromisoformat(item["timestamp"]).date()
        sessions = self.calendar["sessions"]
        for override in self.calendar["sessionOverrides"]:
            if override["date"] == day.isoformat():
                sessions = override["sessions"]
        if day.weekday() >= 5 or day.isoformat() in self.calendar["holidays"] or not sessions:
            raise ValueError("日线不属于实际市场会话")
        close = datetime.combine(day, datetime.min.time(), ZoneInfo(self.timezone)) + timedelta(minutes=sessions[-1]["endMinute"])
        return {**item, "availableAt": self.fetched_at,
                "completionStatus": "complete" if self.observed_at >= close else "incomplete"}
