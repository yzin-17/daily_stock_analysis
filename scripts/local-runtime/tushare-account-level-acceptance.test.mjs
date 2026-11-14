import assert from 'node:assert/strict';
import test from 'node:test';
import { accountLevelReportPath, runAcceptance } from './tushare-account-level-acceptance.mjs';

const output = new URL('../../../thesis-ledger-infra/.maintenance-backups/account-test-new.json', import.meta.url).pathname;
const identity = { Id: 'a'.repeat(64), Image: 'sha256:' + 'b'.repeat(64),
  Config: { Labels: { 'com.docker.compose.project': 'thesis-ledger-dev',
    'com.docker.compose.service': 'dsa', 'org.openai.thesis-ledger.data-runtime': 'dsa-data-runtime-v1' } },
  State: { Running: true, Health: { Status: 'healthy' }, StartedAt: 'original' } };

function fixture(overrides = {}) {
  let saved = false;
  const observed = { passed: true, cleaned: true, realProviderRequests: 0,
    phases: [{}, { points: 120, states: ['allowed'], calls: [] }], ...overrides };
  return { io: { inspect: () => structuredClone(identity),
    execute: () => ({ status: 0, stdout: JSON.stringify(observed) }),
    write: () => { saved = true; } }, saved: () => saved };
}

test('报告路径必须为私有目录中的全新绝对路径', () => {
  assert.equal(accountLevelReportPath([output]), output);
  for (const args of [[], ['/tmp/report.json'], [output, output]]) {
    assert.throws(() => accountLevelReportPath(args));
  }
});

test('受控目标验收成功后写入报告，保持零实源请求', () => {
  const f = fixture();
  assert.equal(runAcceptance([output], f.io).realProviderRequests, 0);
  assert.equal(f.saved(), true);
});

test('存在实源请求或目标重启时拒绝封存报告', () => {
  const f = fixture({ realProviderRequests: 1 });
  assert.throws(() => runAcceptance([output], f.io));
  assert.equal(f.saved(), false);
  const changed = fixture(); let reads = 0;
  changed.io.inspect = () => ({ ...structuredClone(identity), State: { ...identity.State, StartedAt: reads++ ? 'changed' : 'original' } });
  assert.throws(() => runAcceptance([output], changed.io));
  assert.equal(changed.saved(), false);
});
