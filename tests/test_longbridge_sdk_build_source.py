"""固定SDK构建源的导入拒绝与预取边界；不编译或安装SDK。"""
import importlib.util
from pathlib import Path

import pytest


def builder():
    path = Path(__file__).resolve().parents[1] / 'scripts/build_thesis_ledger_longbridge_sdk.py'
    spec = importlib.util.spec_from_file_location('sdk_builder', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('revision,dirty,extra', [('wrong', False, ''), ('valid', True, ''), ('valid', False, 'untracked.py')])
def test_imported_sdk_source_requires_fixed_clean_source(tmp_path, monkeypatch, revision, dirty, extra):
    module = builder()
    upstream = tmp_path / 'input'
    upstream.mkdir()
    (upstream / 'code.rs').write_text('原SDK源码')
    calls = []
    def run(command, **options):
        calls.append(command)
        if command[1] == 'rev-parse': return module.REVISION if revision == 'valid' else revision
        if command[1] == 'diff' and dirty: raise RuntimeError('dirty')
        if command[1] == 'ls-files': return extra
        return ''
    monkeypatch.setattr(module, 'run', run)
    with pytest.raises(RuntimeError):
        module.prepare_source(upstream, tmp_path / 'output', cwd=tmp_path, env={}, log=tmp_path / 'log')
    assert not any(command[1] in {'clone', 'apply'} for command in calls)
    assert (upstream / 'code.rs').read_text() == '原SDK源码'


def test_imported_source_checks_objects_and_restores_index_without_network(tmp_path, monkeypatch):
    module = builder()
    upstream = tmp_path / 'input'
    upstream.mkdir()
    (upstream / 'code.rs').write_text('原SDK源码')
    calls = []
    def run(command, **options):
        calls.append(command)
        return module.REVISION if command[1] == 'rev-parse' else ''
    monkeypatch.setattr(module, 'run', run)
    source = tmp_path / 'source'
    module.prepare_source(upstream, source, cwd=tmp_path, env={}, log=tmp_path / 'log')
    assert calls == [['git', 'rev-parse', 'HEAD'], ['git', 'fsck', '--strict', '--no-reflogs'],
                     ['git', 'reset', '--mixed', 'HEAD'], ['git', 'diff', '--exit-code', 'HEAD'], ['git', 'ls-files', '--others']]
    assert (source / 'code.rs').read_text() == '原SDK源码'


def test_source_fetch_removes_only_transient_git_metadata(tmp_path):
    module = builder()
    source = tmp_path / 'source'
    git = source / '.git'
    (git / 'logs').mkdir(parents=True)
    (git / 'hooks').mkdir()
    (git / 'index').write_text('临时index')
    (git / 'HEAD').write_text(module.REVISION)
    (git / 'config').write_text('固定公开来源配置')
    (source / 'code.rs').write_text('原源码')
    module.normalize_source_input(source)
    assert not (git / 'logs').exists() and not (git / 'hooks').exists() and not (git / 'index').exists()
    assert (git / 'HEAD').read_text() == module.REVISION and (source / 'code.rs').read_text() == '原源码'


def test_fetch_only_stops_before_patch_compiler_and_install(tmp_path, monkeypatch):
    module = builder()
    calls = []
    def prepare(source_dir, source, **options):
        source.mkdir()
        calls.append('fixed_source')
    monkeypatch.setattr(module, 'prepare_source', prepare)
    monkeypatch.setattr(module, 'run', lambda *args, **options: pytest.fail('fetch-only不得应用补丁、编译或安装'))
    monkeypatch.setattr(module.sys, 'argv', ['sdk', '--fetch-only', '--work-dir', str(tmp_path / 'work'), '--output', str(tmp_path / 'output')])
    module.main()
    assert calls == ['fixed_source']
