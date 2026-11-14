import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { verificationPlan, runVerification } from './verify-tencent-daily.mjs';

test('默认计划零请求并拒绝重复参数', () => {
  const plan = verificationPlan(['--date=2026-11-12', '--output=/tmp/tencent-plan']);
  assert.equal(runVerification(plan, { run: () => { throw new Error('不得调用'); } }).providerReads, 0);
  assert.throws(() => verificationPlan(['--date=2026-11-12', '--date=2026-11-12', '--output=/tmp/plan']));
});
test('未知日历禁止读取，许可存在时不能重发', () => {
  const output = mkdtempSync(join(tmpdir(), 'tencent-check-'));
  let calls = 0;
  try {
    const plan = verificationPlan(['--date=2026-11-12', `--output=${output}`, '--execute']);
    const io = { run: () => { calls++; return JSON.stringify({ date: plan.day, state: 'unknown', tradingDayState: 'unknown' }); } };
    assert.equal(runVerification(plan, io).status, 'execution_unknown');
    assert.equal(calls, 1);
    assert.throws(() => runVerification(plan, io), /已有许可/u);
    assert.equal(calls, 1);
  } finally { rmSync(output, { recursive: true }); }
});
test('已知交易日午休允许独立日线验证且保留实际回执', () => {
  const output = mkdtempSync(join(tmpdir(), 'tencent-check-'));
  let prices = 0;
  try {
    const plan = verificationPlan(['--date=2026-11-12', `--output=${output}`, '--execute']);
    const run = (_command, args) => {
      if (args.includes('node')) return JSON.stringify({ date: plan.day, state: 'closed', tradingDayState: 'open' });
      if (args[0] === 'inspect') return JSON.stringify([{ Id: 'fixture', Image: 'image', State: { Running: true, StartedAt: 'fixed' },
        Config: { Labels: { 'com.docker.compose.project': 'thesis-ledger-dev', 'com.docker.compose.service': 'dsa' } } }]);
      prices++;
      return JSON.stringify({ readOnly: true, requestCount: 1, status: 'success' });
    };
    const result = runVerification(plan, { run });
    assert.equal(prices, 1);
    assert.equal(result.status, 'success');
    assert.equal(result.targetUnchanged, true);
  } finally { rmSync(output, { recursive: true }); }
});
