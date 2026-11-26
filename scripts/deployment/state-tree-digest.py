"""计算应用文件树的相对路径及内容摘要，供隔离副本核对。"""
import hashlib
from pathlib import Path
import sys


def digest_tree(directory, ignored):
    root = Path(directory)
    witness = hashlib.sha256()
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root).as_posix()
        if relative in ignored:
            continue
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError('数据文件树包含链接或特殊文件')
        marker = b'd' if path.is_dir() else b'f'
        witness.update(marker + relative.encode('utf-8') + b'\0')
        if path.is_file():
            content = hashlib.sha256()
            with path.open('rb') as source:
                for data in iter(lambda: source.read(1024 * 1024), b''):
                    content.update(data)
            witness.update(content.digest())
    return witness.hexdigest()


if __name__ == '__main__':
    print(digest_tree(sys.argv[1], sys.argv[2:]))
