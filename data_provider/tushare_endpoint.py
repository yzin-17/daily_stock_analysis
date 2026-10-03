"""Tushare 接入地址解析，供凭据快照与 HTTP 客户端共用。"""

import os

DEFAULT_TUSHARE_HTTP_URL = "http://api.tushare.pro"


def _resolve_tushare_http_url():
    raw = os.getenv("TUSHARE_HTTP_URL", "").strip()
    if not raw:
        return None
    if not raw.startswith(("http://", "https://")):
        raise ValueError("TUSHARE_HTTP_URL 必须以 http:// 或 https:// 开头")
    return raw
