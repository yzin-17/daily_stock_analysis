import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { runRecheck, validateEntries } from './failed-provider-recheck.mjs';

const entry = (period = '1m') => ({ key: { market: 'CN', assetType: 'STOCK', adjustment: 'none', timeframe: period },
  target: { providerId: 'pytdx', upstreamSource: 'pytdx' } });
test('预算与暂停来源在执行前拒绝', () => {
  assert.throws(() => validateEntries([entry(), entry()]));
  assert.throws(() => validateEntries([{ ...entry(), target: { providerId: 'tencent' } }]));
  assert.throws(() => validateEntries(Array.from({ length: 13 }, () => entry())));
});
test('未知会话不读取行情；已有独占许可不重发', async () => {
  const output = mkdtempSync(join(tmpdir(), 'failed-recheck-'));
  try {
    let calls = 0;
    const plan = { entries: [entry()], output, notBefore: new Date(0).toISOString(), expiresAt: new Date(Date.now() + 60_000).toISOString() };
    const io = { run: () => { calls++; return JSON.stringify({ sessions: { CN: { state: 'unknown' } } }); } };
    const report = await runRecheck(plan, io);
    assert.equal(report.results[0].reason, 'market_not_open');
    assert.equal(calls, 1);
    await assert.rejects(runRecheck(plan, io), /已有执行许可/u);
    assert.equal(calls, 1);
  } finally { rmSync(output, { recursive: true }); }
});
test('过期窗口不触发任何容器读取', async () => {
  const output = mkdtempSync(join(tmpdir(), 'failed-recheck-'));
  try {
    const report = await runRecheck({ entries: [entry()], output, notBefore: new Date(0).toISOString(), expiresAt: new Date(1).toISOString() },
      { run: () => { throw new Error('不应调用'); } });
    assert.equal(report.results[0].reason, 'outside_window');
  } finally { rmSync(output, { recursive: true }); }
});
test('真实读取的限额拒绝停止同来源市场后续口径', async () => {
  const output = mkdtempSync(join(tmpdir(), 'failed-recheck-'));
  let priceCalls = 0;
  try {
    const run = (_command, args) => {
      if (args[0] === 'inspect') return JSON.stringify([{ Id: 'fixture', Image: 'image', State: { Running: true, StartedAt: 'fixed' },
        Config: { Labels: { 'com.docker.compose.project': 'thesis-ledger-dev', 'com.docker.compose.service': 'dsa' } } }]);
      if (args.includes('node')) return JSON.stringify({ sessions: { CN: { state: 'open' } } });
      priceCalls++;
      return JSON.stringify({ readOnly: true, observations: [{ status: 'failed', errorCode: 'quota_unavailable' }] });
    };
    const report = await runRecheck({ entries: [entry(), entry('60m')], output,
      notBefore: new Date(0).toISOString(), expiresAt: new Date(Date.now() + 60_000).toISOString() }, { run });
    assert.equal(priceCalls, 1);
    assert.equal(report.results[1].reason, 'source_stopped');
  } finally { rmSync(output, { recursive: true }); }
});
