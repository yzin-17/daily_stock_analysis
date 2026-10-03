"""RQData SDK 调用共用的有界子进程 JSON 传输。"""

import json
import math
import multiprocessing
import os
from pathlib import Path
import tempfile
import time


MAX_RESULT_BYTES = 8 * 1024 * 1024


class IsolatedStartError(RuntimeError):
    pass


class IsolatedReadError(RuntimeError):
    pass


class IsolatedTimeoutError(TimeoutError):
    pass


def _read_child(factory, worker, arguments, output):
    # SDK 的异常、原始响应和凭据不得经 stdout/stderr 或父进程异常传播。
    with open(os.devnull, 'w') as sink:
        os.dup2(sink.fileno(), 1)
        os.dup2(sink.fileno(), 2)
    try:
        result = worker(factory, *arguments)
        encoded = json.dumps({'result': result}, allow_nan=False).encode()
        if len(encoded) > MAX_RESULT_BYTES:
            raise ValueError('response budget')
    except Exception:
        encoded = b'{"error":"rqdata_read_failed"}'
    Path(output).write_bytes(encoded)


def read_isolated_json(factory, worker, arguments, *, timeout_seconds, temp_prefix):
    """工厂、SDK 调用、标准化和解码均在同一总期限内。"""
    if (type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 60):
        raise ValueError('rqdata invalid timeout')
    if not callable(factory) or not callable(worker) or not isinstance(arguments, tuple):
        raise ValueError('rqdata invalid isolated request')
    deadline = time.monotonic() + timeout_seconds
    with tempfile.TemporaryDirectory(prefix=temp_prefix) as directory:
        output = str(Path(directory) / 'result.json')
        process = multiprocessing.get_context('spawn').Process(
            target=_read_child, args=(factory, worker, arguments, output), daemon=True,
        )
        try:
            try:
                process.start()
            except Exception:
                raise IsolatedStartError() from None
            process.join(max(0, deadline - time.monotonic()))
            if process.is_alive() or time.monotonic() >= deadline:
                raise IsolatedTimeoutError()
            if process.exitcode != 0 or not Path(output).is_file():
                raise IsolatedReadError()
            encoded = Path(output).read_bytes()
            if len(encoded) > MAX_RESULT_BYTES:
                raise IsolatedReadError()
            try:
                value = json.loads(encoded)
            except (ValueError, UnicodeDecodeError):
                raise IsolatedReadError() from None
            if time.monotonic() >= deadline:
                raise IsolatedTimeoutError()
            if type(value) is not dict or set(value) != {'result'}:
                raise IsolatedReadError()
            return value['result']
        finally:
            if process.pid is not None:
                if process.is_alive():
                    process.terminate()
                    process.join(0.5)
                if process.is_alive():
                    process.kill()
                    process.join(0.5)
                process.close()
