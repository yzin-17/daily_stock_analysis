import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { cpSync, existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

const entry = fileURLToPath(new URL('../local-deployment.sh', import.meta.url));

test('应用声明原生 SDK 上下文，拒绝相对工作目录', () => {
  const result = spawnSync('bash', [entry, 'build-config', '/controlled/build'], { encoding: 'utf8' });
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(JSON.parse(result.stdout), { services: { dsa: { build: {
    additional_contexts: { longbridge_upstream: '/controlled/build/sdk-source' },
  } } } });
  assert.notEqual(spawnSync('bash', [entry, 'build-config', 'relative']).status, 0);
});

test('公开构建准备命令传递缓存配置，失败传播且只清理本轮应用输入', t => {
  const root = mkdtempSync(join(tmpdir(), 'dsa-build-interface-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  mkdirSync(join(root, 'scripts')); mkdirSync(join(root, 'work'));
  cpSync(entry, join(root, 'scripts/local-deployment.sh'));
  writeFileSync(join(root, 'scripts/prepare-local-sdk-source.sh'), `#!/bin/bash
set -eu
[[ "$2" == "$EXPECTED_CACHE" ]]
mkdir -p "$1/sdk-source" "$1/sdk-source-output"
exit "$FETCH_FAIL"
`);
  const work = join(root, 'work'), cache = join(root, 'cache');
  const run = (mode, extra = {}) => spawnSync('bash', [join(root, 'scripts/local-deployment.sh'), mode, work, ...(mode === 'prepare-build' ? [cache] : [])], {
    env: { ...process.env, LONG_BRIDGE_SOURCE_CACHE: cache,
      EXPECTED_CACHE: cache, FETCH_FAIL: '0', ...extra }, encoding: 'utf8',
  });
  assert.equal(run('prepare-build').status, 0);
  assert.equal(run('prepare-build', { FETCH_FAIL: '17' }).status, 17);
  mkdirSync(join(work, 'other-assets'));
  assert.equal(run('cleanup-build').status, 0);
  assert(!existsSync(join(work, 'sdk-source')));
  assert(existsSync(join(work, 'other-assets')));
});
