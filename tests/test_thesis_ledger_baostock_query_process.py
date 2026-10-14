"""独立能力沿来源进程边界执行；SDK连接不能在服务进程共享。"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier
from types import SimpleNamespace
import json
import io
import os
import subprocess

import pytest

from data_provider import baostock_query_process as process
from src.services.thesis_ledger_capability_contracts import CapabilityContractError


def adapter():
    return SimpleNamespace(_convert_stock_code=lambda symbol: 'sh.' + symbol.split('.')[0])


def test_two_capabilities_launch_separate_bounded_processes(monkeypatch):
    barrier, calls = Barrier(2), []

    def run(command, **kwargs):
        payload = json.loads(kwargs['input'])
        calls.append((command, payload, kwargs))
        barrier.wait(timeout=2)
        return SimpleNamespace(stdout=json.dumps({'result': {'mode': payload['mode']}}))

    monkeypatch.setattr(subprocess, 'run', run)
    with ThreadPoolExecutor(2) as pool:
        daily = pool.submit(process.read_daily_status, adapter(), symbol='600519.SH',
                            start=date(2026, 10, 8), end=date(2026, 10, 9))
        dates = pool.submit(process.read_basic_dates, adapter(), '600519.SH')
        assert daily.result() == {'mode': 'daily-status'}
        assert dates.result() == {'mode': 'basic-dates'}
    assert len(calls) == 2
    for command, payload, options in calls:
        assert command[1:] == ['-m', 'data_provider.baostock_query_process']
        assert options['timeout'] == 45 and options['check'] and options['capture_output']
        assert payload['symbol'] == '600519.SH'
    assert 'start' not in next(p for _, p, _ in calls if p['mode'] == 'basic-dates')


@pytest.mark.parametrize('failure,code', [
    (subprocess.TimeoutExpired('child', 45), 'timeout'),
    (subprocess.CalledProcessError(1, 'child'), 'upstream_unavailable'),
])
def test_process_failure_preserves_error_without_source_text(monkeypatch, failure, code):
    def run(*args, **kwargs):
        raise failure
    monkeypatch.setattr(subprocess, 'run', run)
    with pytest.raises(CapabilityContractError) as captured:
        process.read_basic_dates(adapter(), '600519.SH')
    assert captured.value.code == code
    assert 'child' not in str(captured.value)


@pytest.mark.parametrize('payload', ['not-json', '[]'])
def test_invalid_child_response_is_not_unknown_dates(monkeypatch, payload):
    monkeypatch.setattr(subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(stdout=payload))
    with pytest.raises(CapabilityContractError) as captured:
        process.read_basic_dates(adapter(), '600519.SH')
    assert captured.value.code == 'invalid_response'


def test_timeout_reaps_actual_child(monkeypatch):
    run = subprocess.run
    def stalled(*args, **kwargs):
        return run([process.sys.executable, '-c', 'import time; time.sleep(10)'],
                   timeout=0.05, capture_output=True, text=True, check=True)
    monkeypatch.setattr(subprocess, 'run', stalled)
    with pytest.raises(CapabilityContractError) as captured:
        process.read_basic_dates(adapter(), '600519.SH')
    assert captured.value.code == 'timeout'


@pytest.mark.parametrize('mode', ['daily-status', 'basic-dates'])
def test_worker_uses_original_sdk_parsing_and_releases_session(monkeypatch, capsys, mode):
    from tests.test_thesis_ledger_baostock_capability_path import Adapter, DAY, SYMBOL
    instance = Adapter()
    monkeypatch.setattr('data_provider.baostock_fetcher.BaostockFetcher', lambda: instance)
    monkeypatch.setattr(process.sys, 'stdin', io.StringIO(json.dumps({
        'mode': mode, 'symbol': SYMBOL, 'start': DAY, 'end': DAY,
    })))
    process.main()
    value = json.loads(capsys.readouterr().out)['result']
    assert instance.calls[0] == 'session' and instance.calls[-1] == 'released'
    if mode == 'daily-status':
        assert value['days'][0]['state'] == 'suspended' and value['coverage']['complete']
        assert instance.calls == ['session', 'calendar', 'history', 'released']
    else:
        assert value['ipoDate'] == '1999-11-10' and value['outDateStatus'] == 'notProvided'
        assert instance.calls == ['session', 'basic', 'released']


@pytest.mark.parametrize('code', ['invalid_response', 'upstream_unavailable'])
def test_child_error_classification_is_preserved(monkeypatch, code):
    monkeypatch.setattr(subprocess, 'run', lambda *args, **kwargs:
                        SimpleNamespace(stdout=json.dumps({'error': code})))
    with pytest.raises(CapabilityContractError) as captured:
        process.read_basic_dates(adapter(), '600519.SH')
    assert captured.value.code == code


def test_simultaneous_queries_execute_in_distinct_real_child_processes(monkeypatch):
    run, barrier = subprocess.run, Barrier(2)
    code = 'import os,json,sys; json.load(sys.stdin); print(json.dumps({"result":{"pid":os.getpid()}}))'
    def launch(command, **kwargs):
        assert command[1:] == ['-m', 'data_provider.baostock_query_process']
        barrier.wait(timeout=2)
        # 仅替换子进程SDK工作内容；实际启动、stdin/stdout、超时和wait由原subprocess.run执行。
        return run([command[0], '-c', code], **kwargs)
    monkeypatch.setattr(subprocess, 'run', launch)
    with ThreadPoolExecutor(2) as pool:
        a = pool.submit(process.read_basic_dates, adapter(), '600519.SH')
        b = pool.submit(process.read_daily_status, adapter(), symbol='600519.SH',
                        start=date(2026, 10, 8), end=date(2026, 10, 9))
        pids = {a.result()['pid'], b.result()['pid']}
    assert len(pids) == 2 and os.getpid() not in pids
