"""股票索引 HTTP 下载的可终止执行单元；缓存发布仍由父进程负责。"""

import multiprocessing
from threading import Event


class StockIndexRefreshStopped(BaseException):
    """退出停止不进入来源失败计数。"""


class StockIndexCleanupFailed(BaseException):
    """实际执行单元尚存时禁止把退出报告为成功。"""


def stock_index_download_worker(connection, settings):
    try:
        from src.services.stock_index_remote_service import _download_remote_stock_index
        connection.send((True, _download_remote_stock_index(settings)))
    except Exception as exc:
        connection.send((False, f"股票索引远端下载失败，类型={type(exc).__name__}"))
    finally:
        connection.close()


def isolated_stock_index_download(settings, stopping: Event, *, worker=stock_index_download_worker) -> bytes:
    if stopping.is_set():
        raise StockIndexRefreshStopped()
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=worker, args=(child, settings), name="stock-index-download", daemon=True)
    started = False
    try:
        process.start()
        started = True
        child.close()
        while True:
            if stopping.is_set():
                raise StockIndexRefreshStopped()
            if parent.poll(0.1):
                try:
                    success, value = parent.recv()
                except EOFError:
                    raise RuntimeError("股票索引下载进程未返回结果") from None
                if stopping.is_set():
                    raise StockIndexRefreshStopped()
                if not success:
                    raise RuntimeError(value)
                if not isinstance(value, bytes):
                    raise ValueError("股票索引下载响应无效")
                return value
            if not process.is_alive():
                raise RuntimeError("股票索引下载进程未返回结果")
    finally:
        child.close()
        parent.close()
        if started:
            if process.is_alive():
                process.terminate()
                process.join(1)
            if process.is_alive():
                process.kill()
                process.join(1)
            else:
                process.join(1)
            if process.is_alive():
                raise StockIndexCleanupFailed()
        process.close()
