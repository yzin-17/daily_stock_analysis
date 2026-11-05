"""原生队列与产品事件流的运行诊断断言。"""

import unittest
from concurrent.futures import Future
from datetime import datetime

from src.services.task_queue import AnalysisTaskQueue, TaskInfo, TaskStatus
from src.services.run_diagnostics import activate_run_diagnostic_context, record_provider_run, reset_run_diagnostic_context


class _SyncExecutor:
    def submit(self, fn, *args, **kwargs):
        future = Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except Exception as exc:  # pragma: no cover - exercised by queue behavior
            future.set_exception(exc)
        return future

    def shutdown(self, wait=True):
        return None


class NativeRunDiagnosticsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._original_queue = AnalysisTaskQueue._instance
        AnalysisTaskQueue._instance = None

    def tearDown(self) -> None:
        queue = AnalysisTaskQueue._instance
        if queue is not None and queue is not self._original_queue:
            executor = getattr(queue, "_executor", None)
            if executor is not None and hasattr(executor, "shutdown"):
                executor.shutdown(wait=False)
        AnalysisTaskQueue._instance = self._original_queue

    def test_task_info_exposes_trace_id_for_sse_and_status_payloads(self) -> None:
        task = TaskInfo(task_id="task-1", stock_code="600519")

        self.assertEqual(task.to_dict()["trace_id"], "task-1")
        self.assertEqual(task.copy().trace_id, "task-1")

    def test_background_task_reuses_task_id_as_trace_id(self) -> None:
        queue = AnalysisTaskQueue(max_workers=1)
        queue._executor = _SyncExecutor()

        task = queue.submit_background_task(
            lambda: {"ok": True},
            stock_code="market_review",
            task_id="market-task-1",
        )
        stored = queue.get_task(task.task_id)

        self.assertIsNotNone(stored)
        self.assertEqual(stored.trace_id, "market-task-1")
        self.assertEqual(stored.status, TaskStatus.COMPLETED)

    def test_diagnostic_event_sink_receives_provider_llm_history_and_notification_events(self) -> None:
        events = []
        token = activate_run_diagnostic_context(
            trace_id="trace-flow",
            task_id="task-flow",
            query_id="task-flow",
            stock_code="600519",
            event_sink=events.append,
        )
        try:
            record_provider_run(
                data_type="daily_data",
                provider="UnitFetcher",
                operation="get_daily_data",
                success=True,
                latency_ms=12,
                record_count=3,
            )
            from src.services.run_diagnostics import record_history_run, record_llm_run, record_notification_run

            record_llm_run(success=True, model="deepseek-chat", duration_ms=34)
            record_history_run(report_saved=True, metadata_saved=True, analysis_history_id=7)
            record_notification_run(channel="report", status="success", success=True)
        finally:
            reset_run_diagnostic_context(token)

        self.assertEqual(
            [event["type"] for event in events],
            ["provider_run", "llm_run", "history_run", "notification_run"],
        )
        self.assertEqual(events[0]["metadata"]["node"]["lane"], "data_source")
        provider_node = events[0]["metadata"]["node"]
        provider_started = datetime.fromisoformat(provider_node["started_at"])
        provider_ended = datetime.fromisoformat(provider_node["ended_at"])
        self.assertEqual(int((provider_ended - provider_started).total_seconds() * 1000), 12)
        self.assertEqual(events[1]["metadata"]["node"]["kind"], "model")
        llm_node = events[1]["metadata"]["node"]
        llm_started = datetime.fromisoformat(llm_node["started_at"])
        llm_ended = datetime.fromisoformat(llm_node["ended_at"])
        self.assertEqual(int((llm_ended - llm_started).total_seconds() * 1000), 34)
