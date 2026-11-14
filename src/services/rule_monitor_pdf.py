"""PDF 正文解析独立进程，限制 CPU、内存、页数及内容大小。"""

import io
import json
import sys

from src.services.rule_monitor_snapshot import readable_text, text_snapshot, unavailable


def extract(body):
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(body))
    if reader.is_encrypted:
        return unavailable("pdf_encrypted", "pdf")
    if len(reader.pages) > 100:
        return unavailable("text_too_large", "pdf")
    parts = []
    size = 0
    for page in reader.pages:
        content = page.get_contents()
        if content is not None:
            size += len(content.get_data())
            if size > 16 * 1024 * 1024:
                return unavailable("text_too_large", "pdf")
        parts.append(readable_text(page.extract_text() or ""))
        if sum(len(part) for part in parts) > 250_000:
            return unavailable("text_too_large", "pdf")
    return text_snapshot("pdf", "\n\n".join(parts))


if __name__ == "__main__":
    import resource
    resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
    if sys.platform == "linux":
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    try:
        result = extract(sys.stdin.buffer.read(2 * 1024 * 1024 + 1))
    except Exception:
        result = unavailable("extraction_failed", "pdf")
    print(json.dumps(result, ensure_ascii=False))
