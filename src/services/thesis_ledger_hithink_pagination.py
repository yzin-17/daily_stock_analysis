"""HiThink 历史响应的分页完成边界。"""

import re
from typing import Any, Mapping


_PAGINATION_FIELDS = {
    "continuationtoken", "cursor", "hasmore", "limit", "next", "nextcursor",
    "nextpage", "nexttoken", "offset", "page", "pagesize", "pagetoken",
    "total", "totalpages",
}


def has_unverified_pagination(container: Any) -> bool:
    """未实现分页遍历时，显式分页信息不能成为完整响应证明。"""
    if isinstance(container, Mapping):
        for key, value in container.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).casefold())
            if normalized in _PAGINATION_FIELDS or normalized.startswith(("nextpage", "nextcursor")):
                if normalized == "hasmore" and not bool(value):
                    continue
                return True
            if has_unverified_pagination(value):
                return True
    elif isinstance(container, list):
        return any(has_unverified_pagination(item) for item in container)
    return False
