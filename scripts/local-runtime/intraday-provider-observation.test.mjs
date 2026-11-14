import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { observationPlan, runObservation } from './intraday-provider-observation.mjs';

test('日期、跨日美股窗口和无执行模式', async () => {
  const plan = observationPlan(['--phase=us', '--output=/tmp/sample']);
  assert.equal(plan.slots.at(-1), '2026-11-12T00:05:00+08:00');
  assert.equal((await runObservation(plan)).maxLogicalReadsPerProviderMarket, 12);
  assert.throws(() => observationPlan(['--phase=other', '--output=/tmp/sample']));
});

test('已未知的执行结果阻断后续读取，不能重跑扩大预算', async () => {
  const output = mkdtempSync(join(tmpdir(), 'intraday-observation-'));
  const plan = observationPlan(['--phase=asia', `--output=${output}`, '--execute']);
  writeFileSync(join(output, 'asia-1.json'), JSON.stringify({ phase: 'asia', scheduledAt: plan.slots[0], status: 'execution_unknown' }));
  try {
    const result = await runObservation(plan, { now: () => Date.parse(plan.slots[0]),
      run: () => assert.fail('未知结果后不得再次执行') });
    assert.equal(result.finished, false);
    assert.deepEqual(result.statuses, ['execution_unknown']);
  } finally { rmSync(output, { recursive: true }); }
});

test('四批使用累计回执；重跑复用结果，不重复请求', async () => {
  const output = mkdtempSync(join(tmpdir(), 'intraday-observation-'));
  const plan = observationPlan(['--phase=asia', `--output=${output}`, '--execute']);
  let time = Date.parse(plan.slots[0]);
  let requests = 0;
  const identity = [{ Id: 'safe-id', Image: 'safe-image', State: { Running: true, StartedAt: 'same' },
    Config: { Labels: { 'com.docker.compose.service': 'dsa', 'com.docker.compose.project': 'thesis-ledger-dev' } } }];
  const io = { now: () => time, wait: async (ms) => { time += ms; },
    run: (_command, args, input) => {
      if (args[0] === 'inspect') return JSON.stringify(identity);
      requests += 1;
      const previous = input ? JSON.parse(input) : { batches: [], observations: [] };
      return JSON.stringify({ readOnly: true, batches: [...previous.batches, time], observations: [] });
    } };
  try {
    assert.equal((await runObservation(plan, io)).finished, true);
    assert.equal(requests, 4);
    assert.equal(JSON.parse(readFileSync(join(output, 'asia-4.json'))).observation.batches.length, 4);
    await runObservation(plan, io);
    assert.equal(requests, 4);
  } finally { rmSync(output, { recursive: true }); }
});
