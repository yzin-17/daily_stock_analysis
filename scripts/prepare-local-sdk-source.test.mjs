import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { prepareLocalSource, sourceMetadata } from './prepare-local-sdk-source.mjs';

function fixture(t, options = {}) {
  const root = mkdtempSync(join(tmpdir(), 'dsa-node-source-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const calls = [];
  const run = args => {
    calls.push(args);
    if (args[0] === 'clone') {
      const path = args.at(-1); mkdirSync(join(path, '.git/logs'), { recursive: true }); mkdirSync(join(path, '.git/hooks'));
      writeFileSync(join(path, '.git/index'), 'transient'); writeFileSync(join(path, '.git/HEAD'), sourceMetadata().revision); writeFileSync(join(path, 'code.rs'), '原 SDK 字节');
    }
    if (args[0] === 'rev-parse') return options.revision ?? sourceMetadata().revision;
    if (args[0] === 'fsck' && options.invalidObjects) throw new Error('对象损坏');
    if (args[0] === 'diff' && options.dirty) throw new Error('工作树变化');
    return args[0] === 'ls-files' ? options.untracked ?? '' : '';
  };
  return { root, cache: join(root, 'cache'), calls, run };
}

test('固定源获取和缓存复用保持版本、对象、工作树及精确规范化', t => {
  const f = fixture(t), work = join(f.root, 'first');
  const result = prepareLocalSource(work, f.cache, { run: f.run, platform: 'linux', log() {} });
  assert.equal(readFileSync(join(result.source, 'code.rs'), 'utf8'), '原 SDK 字节');
  assert(!existsSync(join(result.source, '.git/index'))); assert(!existsSync(join(result.source, '.git/hooks')));
  assert(existsSync(join(result.source, '.git/HEAD')));
  prepareLocalSource(join(f.root, 'second'), f.cache, { run: f.run, platform: 'linux', log() {} });
  assert.equal(f.calls.filter(args => args[0] === 'clone').length, 1);
  assert.equal(f.calls.filter(args => args[0] === 'fsck').length, 2);
  assert.throws(() => prepareLocalSource(work, f.cache, { run: f.run, platform: 'linux' }), /拒绝覆盖/);
});

for (const options of [{ revision: 'wrong' }, { invalidObjects: true }, { dirty: true }, { untracked: 'extra' }]) {
  test(`受控源拒绝错误输入：${Object.keys(options)[0]}`, t => {
    const f = fixture(t, options);
    assert.throws(() => prepareLocalSource(join(f.root, 'work'), f.cache, { run: f.run, platform: 'linux', log() {} }));
    assert(!existsSync(f.cache));
  });
}

test('原 Shell 命令在没有虚拟环境且旧 Python 指向失败程序时仍能运行', t => {
  const f = fixture(t), scripts = join(f.root, 'app/scripts'), bin = join(f.root, 'bin');
  mkdirSync(scripts, { recursive: true }); mkdirSync(bin);
  const original = dirname(fileURLToPath(import.meta.url));
  for (const name of ['prepare-local-sdk-source.sh', 'prepare-local-sdk-source.mjs', 'sdk-source.json']) cpSync(join(original, name), join(scripts, name));
  writeFileSync(join(bin, 'python'), '#!/bin/sh\ntouch "$PYTHON_CALLED"\nexit 97\n', { mode: 0o755 });
  writeFileSync(join(bin, 'git'), `#!/usr/bin/env node
const fs=require('node:fs'),path=require('node:path'),a=process.argv.slice(2);
if(a[0]==='clone'){fs.mkdirSync(path.join(a.at(-1),'.git'),{recursive:true});fs.writeFileSync(path.join(a.at(-1),'code.rs'),'source');}
if(a[0]==='rev-parse')process.stdout.write(${JSON.stringify(sourceMetadata().revision)});
`, { mode: 0o755 });
  const marker = join(f.root, 'python-called');
  const result = spawnSync('sh', [join(scripts, 'prepare-local-sdk-source.sh'), join(f.root, 'work'), f.cache], {
    env: { ...process.env, PATH: bin + ':' + dirname(process.execPath) + ':' + process.env.PATH,
      LONG_BRIDGE_SOURCE_PYTHON: join(bin, 'python'), PYTHON_CALLED: marker, HTTPS_PROXY: 'http://controlled.invalid' }, encoding: 'utf8',
  });
  assert.equal(result.status, 0, result.stderr); assert(!existsSync(marker));
});

test('SDK 和载荷维护模块可从标准输入导入，不触发源码获取或 CLI', () => {
  const modules = ['./prepare-local-sdk-source.mjs', './deployment/dsa-code-payload.mjs'].map(path => new URL(path, import.meta.url).href);
  const result = spawnSync(process.execPath, ['--input-type=module', '-'], { encoding: 'utf8',
    input: `for(const url of ${JSON.stringify(modules)})await import(url);process.stdout.write('imported');` });
  assert.equal(result.status, 0, result.stderr); assert.equal(result.stdout, 'imported');
});
