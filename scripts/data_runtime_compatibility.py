"""为正式数据镜像记录依赖、原生 SDK 与数据库结构输入的原字节指纹。"""

import argparse
from hashlib import sha256
import json
from pathlib import Path


def compatibility_inventory(root):
    root = Path(root).resolve()
    contract = json.loads((root / "scripts/data-runtime-contract.json").read_text())
    if contract["contractVersion"] != "dsa-data-runtime-v1":
        raise ValueError("数据运行合同版本无效")
    files = {}
    for value in contract["compatibilityInputs"]:
        path = root / value
        if not path.exists() or not path.resolve().is_relative_to(root):
            raise ValueError("兼容性输入缺失或越界")
        candidates = path.rglob("*") if path.is_dir() else (path,)
        for candidate in candidates:
            if any(part in {".git", "__pycache__"} for part in candidate.relative_to(root).parts):
                continue
            if not candidate.is_file():
                continue
            if candidate.is_symlink() or not candidate.resolve().is_relative_to(root):
                raise ValueError("兼容性输入不能使用外部符号链接")
            name = candidate.relative_to(root).as_posix()
            files[name] = sha256(candidate.read_bytes()).hexdigest()
    if not files:
        raise ValueError("兼容性输入不能为空")
    return {"schemaVersion": "dsa-runtime-compatibility-v1", "contractVersion": contract["contractVersion"],
            "files": dict(sorted(files.items()))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    options = parser.parse_args()
    text = json.dumps(compatibility_inventory(options.root), ensure_ascii=False, sort_keys=True) + "\n"
    if options.output:
        options.output.write_text(text)
    else:
        print(text, end="")


if __name__ == "__main__":
    main()
