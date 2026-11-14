import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { main, parseSplitAdmissionArguments, reviewedDigest, splitTargetIdentity } from './eastmoney-split-admission.mjs';

const confirm = '--confirm=thesis-ledger-dev/159516.SZ/2026-03-30/2026-07-10';
const hash = `--sha256=${reviewedDigest}`;
const original = readFileSync(new URL('../../../thesis-ledger/docs/evidence/2026-10-02-m22-159516-split-mapping.json', import.meta.url));
const container = () => ({
  Name: '/thesis-ledger-dev-dsa-1', Id: 'a'.repeat(64), Image: `sha256:${'b'.repeat(64)}`,
  Config: { Labels: { 'com.docker.compose.project': 'thesis-ledger-dev', 'com.docker.compose.service': 'dsa' } },
  State: { Running: true, Health: { Status: 'healthy' } },
  Mounts: [{ Type: 'volume', Name: 'thesis-ledger-dsa-data', Destination: '/app/data', Source: '/volume', RW: true }],
});

test('真实入口默认纯计划；PATH无Docker仍只读原材料', () => {
  const saved = process.env.PATH;
  process.env.PATH = '';
  try {
    const plan = main([confirm, hash]);
    assert.equal(plan.mode, 'plan');
    assert.equal(plan.status, 'awaiting-approval');
    assert.equal(plan.recordedBy, null);
    assert.equal(plan.validHours, 24);
    assert.deepEqual(plan.writes, { admission: false, evidence: false, policy: false, credentials: false });
    assert.equal(plan.coverageComplete, false);
    assert.deepEqual(plan.mappings.map(row => row.effectiveDate), ['2026-03-30', '2026-07-10']);
  } finally { process.env.PATH = saved; }
});

test('拒绝扩大证券/窗口、缺摘要、重复或未知选项；不读取材料', () => {
  const cases = [[confirm], [confirm.replace('159516', '510300'), hash],
    [confirm.replace('2026-07-10', '2026-07-11'), hash], [confirm, '--sha256=' + '0'.repeat(64)],
    [confirm, hash, hash], [confirm, hash, '--apply', '--apply'], [confirm, hash, '--force'],
    [confirm, hash, '--valid-hours=25'], [confirm, hash, '--valid-hours=0'],
    [confirm, hash, '--valid-hours=01'], [confirm, hash, '--recorded-by='],
    [confirm, hash, '--recorded-by=two people']];
  for (const args of cases) {
    let reads = 0;
    assert.throws(() => main(args, () => { reads += 1; return original; }));
    assert.equal(reads, 0);
  }
});

test('应用参数必须显式批准有效期与记录人；这里只解析，不执行apply', () => {
  for (const args of [[confirm, hash, '--apply'], [confirm, hash, '--apply', '--valid-hours=24'],
    [confirm, hash, '--apply', '--recorded-by=offline-contract-test']]) {
    assert.throws(() => parseSplitAdmissionArguments(args));
  }
  assert.deepEqual(parseSplitAdmissionArguments([confirm, hash, '--valid-hours=1', '--recorded-by=offline-contract-test', '--apply']),
    { apply: true, validHours: 1, recordedBy: 'offline-contract-test' });
});

test('原字节变化拒绝，不能重格式化后继续消费同一批准摘要', () => {
  assert.throws(() => main([confirm, hash], () => Buffer.from(original.toString().trim())));
  assert.throws(() => main([confirm, hash], () => Buffer.from('{}')));
});

test('拒绝错误项目/服务、停止或不健康容器、错误持久卷和只读挂载', () => {
  assert.equal(splitTargetIdentity(container()).id, 'a'.repeat(64));
  for (const change of [
    x => { x.Name = '/other-dsa-1'; },
    x => { x.Config.Labels['com.docker.compose.project'] = 'other'; },
    x => { x.Config.Labels['com.docker.compose.service'] = 'worker'; },
    x => { x.State.Running = false; }, x => { x.State.Health.Status = 'unhealthy'; },
    x => { x.Mounts[0].Name = 'other'; }, x => { x.Mounts[0].Destination = '/data'; },
    x => { x.Mounts[0].RW = false; }, x => { x.Id = 'short'; },
  ]) {
    const value = container(); change(value); assert.throws(() => splitTargetIdentity(value));
  }
});
