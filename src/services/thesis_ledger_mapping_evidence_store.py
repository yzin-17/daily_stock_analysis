"""逐事件映射证据的内容寻址存储；存储操作不授予来源准入。"""

from hashlib import sha256
import os
from pathlib import Path
import re
import stat
import tempfile

MAX_BYTES = 1024 * 1024


class MappingEvidenceStore:
    def __init__(self, root: Path):
        self.root = root

    @staticmethod
    def _digest(reference: str) -> str:
        if not isinstance(reference, str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", reference):
            raise ValueError("映射证据引用格式无效")
        return reference[7:]

    def read(self, reference: str) -> bytes:
        digest = self._digest(reference)
        path = self.root / f"{digest}.json"
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_BYTES:
                raise ValueError("映射证据文件类型或大小无效")
            content = stream.read(MAX_BYTES + 1)
        if len(content) > MAX_BYTES or sha256(content).hexdigest() != digest:
            raise ValueError("映射证据摘要不匹配")
        return content

    def put(self, content: bytes) -> str:
        if not isinstance(content, bytes) or not 0 < len(content) <= MAX_BYTES:
            raise ValueError("映射证据内容大小无效")
        digest = sha256(content).hexdigest()
        reference = f"sha256:{digest}"
        self.root.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".mapping-", dir=self.root)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, self.root / f"{digest}.json")
            except FileExistsError:
                # 同一摘要只能复用完全相同的原字节，不修复或覆盖被篡改文件。
                if self.read(reference) != content:
                    raise ValueError("映射证据已存在但内容冲突")
        finally:
            Path(temporary).unlink(missing_ok=True)
        return reference
