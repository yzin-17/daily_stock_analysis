import assert from 'node:assert/strict';
import test from 'node:test';
import { sourceCheckPlan, runSourceCheck } from './astock-existing-source-check.mjs';

const args = ['--case=report-eps', '--symbol=600519.SH', '--output=/tmp/astock-check'];
test('固定样本类型、规范身份和绝对证据路径', () => {
  for (const input of [[], args.concat('--unsafe=true'), args.concat('--case=lpr'),
    args.map(x => x.replace('report-eps', 'arbitrary-url')),
    args.map(x => x.replace('600519.SH', '600519')),
    args.map(x => x.replace('/tmp/astock-check', 'relative')),
    args.concat('--start=2026-09-31'), args.concat('--start=2026-10-01', '--end=2026-09-01')])
    assert.throws(() => sourceCheckPlan(input));
});
test('计划模式不执行 Docker 或外部来源', () => {
  const result = runSourceCheck(sourceCheckPlan(args));
  assert.equal(result.execute, false);
  assert.equal(result.providerRequests, 1);
  assert.equal(result.writesConfiguration, false);
  assert.equal(result.newCostUSD, 0);
});
