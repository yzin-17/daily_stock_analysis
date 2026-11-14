import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { assertRecoveryConfig, fingerprint, recoveryOverride, snapshotFromInspection, validateSnapshot } from './runtime-snapshot.mjs';

const runtime = {
  project: 'thesis-ledger-dev', service: 'dsa', imageId: 'sha256:' + 'a'.repeat(64),
  command: ['python', '-m', 'uvicorn', 'server:app', '--host', '0.0.0.0', '--port', '8000'],
  entrypoint: ['/usr/local/bin/docker-entrypoint.sh'],
  healthcheck: { Test: ['CMD', 'curl', '-f', 'http://localhost:8000/health'], Retries: 12 },
  mounts: [{ Type: 'volume', Name: 'owned-data', Destination: '/app/data', RW: true }], ports: {},
};
const identity = { dockerContext: 'desktop-linux', dockerEndpoint: 'unix:///controlled.sock', envSha256: fingerprint('x') };

test('当前与既有 serve-only 快照合同保持兼容，恢复覆盖完整', () => {
  for (const command of [runtime.command, ['python', 'main.py', '--serve-only', '--host', '0.0.0.0', '--port', '8000']]) {
    const snapshot = snapshotFromInspection({ ...runtime, command }, identity);
    assertRecoveryConfig({ name: snapshot.project, ...recoveryOverride(snapshot) }, snapshot);
  }
});

test('拒绝定时分析、跨项目、未知挂载、健康形式及端口漂移', () => {
  const snapshot = snapshotFromInspection(runtime, identity);
  for (const bad of [
    { ...snapshot, command: ['python', 'main.py', '--schedule'] },
    { ...snapshot, project: 'other' },
    { ...snapshot, mounts: [{ Type: 'tmpfs', Destination: '/app/data', RW: true }] },
    { ...snapshot, healthcheck: { Test: ['NONE'] } },
  ]) assert.throws(() => validateSnapshot(bad));
  const config = { name: snapshot.project, ...recoveryOverride(snapshot) };
  config.services.dsa.ports = [{ target: 8000, published: '8888', host_ip: '0.0.0.0' }];
  assert.throws(() => assertRecoveryConfig(config, snapshot), /端口/);
});

test('命令合同通过 JSON 标准输入验证，未知操作与错误输入失败', () => {
  const command = fileURLToPath(new URL('./runtime-contract.mjs', import.meta.url));
  const run = (operation, input) => spawnSync(process.execPath, [command, operation], { input: JSON.stringify(input), encoding: 'utf8' });
  const snapshot = snapshotFromInspection(runtime, identity);
  assert.equal(run('validate', [snapshot]).status, 0);
  assert.equal(run('constructor', [snapshot]).status, 1);
  assert.equal(run('validate', {}).status, 1);
  assert.equal(run('validate', [{ ...snapshot, imageId: 'short' }]).status, 1);
});
