"""在断网临时数据目录中验证候选镜像能读取原存储，禁止修改源挂载。"""

import json
import os
from pathlib import Path, PurePosixPath
import runpy
import shutil
import socket
import sys
import tarfile


def restore_files(archive_path, root):
    root = Path(root).resolve()
    with tarfile.open(archive_path) as archive:
        for member in archive:
            name = PurePosixPath(member.name)
            if name.is_absolute() or '..' in name.parts or not (member.isdir() or member.isfile()):
                raise ValueError('恢复归档包含不安全路径或对象')
            target = root.joinpath(*name.parts)
            if not target.resolve().is_relative_to(root):
                raise ValueError('恢复归档路径越界')
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as source, target.open('xb') as output:
                    shutil.copyfileobj(source, output)


def main():
    assert os.environ.get('DATABASE_PATH') == '/app/data/stock_analysis.db'
    sys.path.insert(0, '/app')  # 应用模块必须从候选镜像读取，不能从挂载的验收工具目录读取。
    root = Path('/app/data')
    assert root.is_dir() and not any(root.iterdir()), '仅允许空临时数据目录'
    restore_files('/probe/dsa-data.tar', root)
    shutil.copyfile('/probe/dsa.sqlite', root / 'stock_analysis.db')

    def blocked(*args, **kwargs):
        raise RuntimeError('隔离恢复验证禁止访问外网')
    socket.create_connection = blocked
    socket.socket.connect = blocked
    socket.socket.connect_ex = blocked
    baseline = json.loads(Path('/probe/baseline.json').read_text())
    retention = runpy.run_path('/probe/retention.py')
    retention['verify_retained'](baseline)
    from src.services.thesis_ledger_control import ThesisLedgerControlStore
    store = ThesisLedgerControlStore(str(root / 'stock_analysis.db'))
    store.effective_policy_v3()
    verified = retention['verify_retained'](baseline)
    import importlib.metadata as metadata
    import futu
    from longbridge.openapi import OAuthBuilder
    assert futu.OpenQuoteContext and OAuthBuilder
    result = {'passed': True, 'networkRequests': 0, 'python': sys.version.split()[0],
              'tables': len(verified['tables']), 'objects': len(verified['objects']),
              'sdk': {name: metadata.version(name) for name in ['futu-api', 'longbridge', 'pypdf']}}
    print('THESIS_FORWARD_PROBE=' + json.dumps(result))


if __name__ == '__main__':
    main()
