"""仅清理DSA源码目录的可再生成Python字节码；默认预览。"""
import json
import os
import sys
from pathlib import Path

ROOTS = tuple(Path("/app") / name for name in ("api", "src", "data_provider", "bot"))


def prune_bytecode(roots, apply=False):
    removed, size = 0, 0
    for root in roots:
        if root.is_symlink():
            raise ValueError("源码目录不能是符号链接")
        for base, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = [d for d in dirs if not (Path(base) / d).is_symlink()]
            if Path(base).name != "__pycache__":
                continue
            for name in files:
                path = Path(base) / name
                if not name.endswith(".pyc") or path.is_symlink():
                    continue
                try:
                    current_size = path.stat().st_size
                    if apply:
                        path.unlink()
                except FileNotFoundError:
                    continue
                removed += 1
                size += current_size
    return {"apply": apply, "files": removed, "bytes": size}


if __name__ == "__main__":
    if sys.argv[1:] not in ([], ["--apply"]):
        raise SystemExit("仅支持默认预览或--apply")
    print(json.dumps(prune_bytecode(ROOTS, sys.argv[1:] == ["--apply"])))
