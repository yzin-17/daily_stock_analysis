"""资料监测拥有的有界纯文本快照；解析失败不改变原指纹语义。"""

import json
import re
import subprocess
import sys

MAX_TEXT = 250_000


def unavailable(reason, format=None):
    return {"format": format, "text": None, "reason": reason}


def text_snapshot(format, text):
    if len(text.encode("utf-16-le")) // 2 > MAX_TEXT:
        return unavailable("text_too_large", format)
    if not text.strip():
        return unavailable("text_unavailable", format)
    return {"format": format, "text": text, "reason": None}


def readable_text(text):
    return "\n".join(line for part in text.splitlines()
        if (line := re.sub(r"\s+", " ", part).strip()))


def pdf_snapshot(body):
    try:
        result = subprocess.run([sys.executable, "-m", "src.services.rule_monitor_pdf"], input=body,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=6, check=True)
        if len(result.stdout) > 2 * 1024 * 1024:
            return unavailable("text_too_large", "pdf")
        return json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        return unavailable("extraction_timeout", "pdf")
    except (subprocess.CalledProcessError, OSError, ValueError):
        return unavailable("extraction_failed", "pdf")
