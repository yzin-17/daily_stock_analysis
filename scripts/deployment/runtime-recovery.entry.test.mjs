import assert from 'node:assert/strict';
import { execFileSync, spawnSync } from 'node:child_process';
import { cpSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

const scripts = fileURLToPath(new URL('../../../thesis-ledger-infra/scripts/', import.meta.url));
const fakeDocker = `#!/usr/bin/env node
const fs = require('node:fs');
const path = require('node:path');
const args = process.argv.slice(2);
const state = JSON.parse(fs.readFileSync(process.env.DSA_TEST_STATE));
fs.appendFileSync(process.env.DSA_TEST_LOG, JSON.stringify(args) + '\\n');
const a = args[0] === '--context' ? args.slice(2) : args;
const out = (v) => process.stdout.write(typeof v === 'string' ? v : JSON.stringify(v));
if (a[0] === 'context') out(a[1] === 'show' ? 'desktop-linux' : 'unix://' + process.env.DSA_TEST_STATE);
else if (a[0] === 'image') out(state.imageId);
else if (a[0] === 'volume') out([{}]);
else if (a[0] === 'inspect') out(a.includes('{{json .State}}') ? {Running: true, Health: {Status: state.health}} : state.runtime);
else if (a[0] === 'compose' && a.includes('ps')) out('owned-dsa');
else if (a[0] === 'compose' && a.includes('config')) {
 const override = JSON.parse(fs.readFileSync(a[a.lastIndexOf('-f') + 1]));
 out({name:'thesis-ledger-dev', ...override});
} else if (a[0] === 'compose' && a.includes('up')) {
 state.runtime = state.originalRuntime;
 fs.writeFileSync(process.env.DSA_TEST_STATE, JSON.stringify(state));
} else process.exit(90);
`;

function fixture(t, { launcher = false } = {}) {
  const root = mkdtempSync(join(tmpdir(), 'dsa-recovery-entry-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  mkdirSync(join(root, 'scripts')); mkdirSync(join(root, 'bin'));
  cpSync(scripts, join(root, 'scripts'), { recursive: true, filter: path => !path.includes('.test.') && !path.includes('/fixtures') });
  writeFileSync(join(root, 'compose.yml'), 'name: thesis-ledger-dev\n');
  writeFileSync(join(root, 'compose.dev.yml'), 'services: {}\n');
  const envFile = join(root, '.env');
  writeFileSync(envFile, 'FAKE_TEST_SECRET=do-not-persist-this\n');
  writeFileSync(join(root, 'bin/docker'), fakeDocker, { mode: 0o755 });
  if (launcher) {
    assert.match(process.execPath, /^[A-Za-z0-9/._-]+$/);
    writeFileSync(join(root, 'bin/node'), `#!/usr/bin/env bash\n"${process.execPath}" "$@" &\nwait $!\n`, { mode: 0o755 });
  }
  const runtime = { project: 'thesis-ledger-dev', service: 'dsa', imageId: 'sha256:' + 'a'.repeat(64),
    command: ['python', 'main.py', '--serve-only', '--host', '0.0.0.0', '--port', '8000'],
    entrypoint: ['/usr/local/bin/docker-entrypoint.sh'],
    healthcheck: { Test: ['CMD', 'curl', '-f', 'http://localhost:8000/health'], Interval: 10_000_000_000, Retries: 12 },
    mounts: ['/app/data', '/app/logs', '/app/reports'].map((Destination, n) => ({
      Type: 'volume', Name: 'owned-test-volume-' + n, Destination, RW: true })), ports: {} };
  const stateFile = join(root, 'state.json'); const log = join(root, 'calls.jsonl');
  writeFileSync(stateFile, JSON.stringify({ runtime, originalRuntime: runtime, imageId: runtime.imageId, health: 'healthy' }));
  writeFileSync(log, '');
  const env = { PATH: join(root, 'bin') + ':' + process.env.PATH, HOME: root, TMPDIR: root,
    DSA_SOURCE_DIR: join(scripts, '../../daily-stock-analysis'), ENV_FILE: envFile, DSA_TEST_STATE: stateFile, DSA_TEST_LOG: log };
  const capture = `set -Eeuo pipefail
node scripts/infra.mjs locked -- node "$PWD/scripts/dsa-runtime-recovery.mjs" snapshot "$PWD" "$ENV_FILE" @update-lock
`;
  execFileSync('bash', ['-c', capture], { cwd: root, env, stdio: ['ignore', 'pipe', 'pipe'] });
  const snapshots = execFileSync('node', ['-e',
    'process.stdout.write(require("fs").readdirSync(process.argv[1]).filter(n => /^\\d+-/.test(n)).join("\\n"))',
    join(root, '.maintenance-backups/dsa-runtime')], { env, encoding: 'utf8' }).trim();
  const snapshot = join(root, '.maintenance-backups/dsa-runtime', snapshots);
  return { root, envFile, env, stateFile, log, snapshot };
}

test('真实恢复脚本持锁，仅执行固定镜像的 DSA 重建，保留所有原卷', (t) => {
  const f = fixture(t);
  assert.ok(!readFileSync(f.snapshot, 'utf8').includes('do-not-persist-this'));
  const result = spawnSync('bash', ['scripts/restore-dsa.sh', f.snapshot], { cwd: f.root, env: f.env, encoding: 'utf8' });
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /已恢复并通过/);
  const calls = readFileSync(f.log, 'utf8').trim().split('\n').map(JSON.parse);
  const ups = calls.filter((args) => args.includes('up'));
  assert.equal(ups.length, 1);
  assert.deepEqual(ups[0].slice(ups[0].indexOf('up')), ['up', '-d', '--no-deps', '--no-build', '--pull', 'never', '--force-recreate', 'dsa']);
  assert.ok(!calls.some((args) => ['build', 'prune', 'rm', 'down', 'run'].some((word) => args.includes(word))));
});

test('真实恢复脚本在环境文件漂移时拒绝，未调用 up', (t) => {
  const f = fixture(t);
  writeFileSync(f.envFile, 'changed=1');
  const result = spawnSync('bash', ['scripts/restore-dsa.sh', f.snapshot], { cwd: f.root, env: f.env, encoding: 'utf8' });
  assert.equal(result.status, 1);
  assert.match(result.stderr, /环境文件已变化/);
  assert.ok(!readFileSync(f.log, 'utf8').includes('"up"'));
});

test('绕过正式入口、没有父进程更新锁时不能恢复', (t) => {
  const f = fixture(t);
  const result = spawnSync('node', ['scripts/dsa-runtime-recovery.mjs', 'restore', f.root,
    f.envFile, f.root, f.snapshot, '1'], { cwd: f.root, env: f.env, encoding: 'utf8' });
  assert.equal(result.status, 1);
  assert.ok(!readFileSync(f.log, 'utf8').includes('"up"'));
});

test('Node 启动器增加进程层级时仍核对真实持锁祖先进程', (t) => {
  const f = fixture(t, { launcher: true });
  const result = spawnSync('bash', ['scripts/restore-dsa.sh', f.snapshot], { cwd: f.root, env: f.env, encoding: 'utf8' });
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /已恢复并通过/);
});
