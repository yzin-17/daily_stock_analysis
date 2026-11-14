import assert from 'node:assert/strict';
import test from 'node:test';
import { main, reportAdmissionPlan, reportTargetIdentity } from './report-metadata-admission.mjs';

const args = ['--symbol=600519.SH', '--start=2026-08-01', '--end=2026-10-23',
  '--confirm=thesis-ledger-dev/akshare/eastmoney/600519.SH/2026-08-01/2026-10-23'];
const apply = ['--apply', '--sha256=' + 'a'.repeat(64), '--valid-hours=24', '--recorded-by=codex-local-research'];

test('默认计划不调用Docker或写入，预算固定为单页两条', () => {
  const value = main(args);
  assert.equal(value.mode, 'plan'); assert.equal(value.writes, false);
  assert.equal(value.pageSize, 2); assert.equal(value.requestBudgetMs, 3000);
});
test('应用必须明确确认摘要、记录人、有效期和完整目标', () => {
  assert.equal(reportAdmissionPlan([...args, ...apply]).mode, 'apply');
  for (const missing of ['--sha256=', '--valid-hours=', '--recorded-by=']) {
    assert.throws(() => reportAdmissionPlan([...args, ...apply.filter(arg => !arg.startsWith(missing))]));
  }
  assert.throws(() => reportAdmissionPlan([...args.slice(0, 3), '--confirm=another-project', ...apply]));
});
test('拒绝重复、未知、冲突模式及扩大预算的参数', () => {
  for (const extra of [['--collect', '--apply'], ['--collect', '--collect'], ['--page-size=20'], ['--timeout=10']]) {
    assert.throws(() => reportAdmissionPlan([...args, ...extra]));
  }
  for (const hours of ['0', '25', '1.5', '024']) {
    assert.throws(() => reportAdmissionPlan([...args, ...apply.filter(arg => !arg.startsWith('--valid-hours=')), `--valid-hours=${hours}`]));
  }
});
test('拒绝非规范日期及标的，不在计划中修正输入', () => {
  for (const [index, value] of [[0, '--symbol=600519'], [1, '--start=2026-02-30'], [1, '--start=2026-11-01']]) {
    const invalid = [...args]; invalid[index] = value;
    assert.throws(() => reportAdmissionPlan(invalid));
  }
});
test('写入前必须匹配本地项目、健康状态与原持久卷', () => {
  const container = { Name: '/thesis-ledger-dev-dsa-1', Id: 'a'.repeat(64), Image: 'sha256:' + 'b'.repeat(64),
    Config: { Labels: { 'com.docker.compose.project': 'thesis-ledger-dev', 'com.docker.compose.service': 'dsa' } },
    State: { Running: true, Health: { Status: 'healthy' } },
    Mounts: [{ Type: 'volume', Name: 'thesis-ledger-dsa-data', Destination: '/app/data', RW: true }] };
  assert.equal(reportTargetIdentity(container).id, container.Id);
  assert.throws(() => reportTargetIdentity({ ...container, Mounts: [] }));
  assert.throws(() => reportTargetIdentity({ ...container, Name: '/another-dsa-1' }));
  assert.throws(() => reportTargetIdentity({ ...container, State: { Running: false } }));
});
