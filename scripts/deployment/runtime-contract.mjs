import { readFileSync } from 'node:fs';
import { assertRecoveryConfig, assertRestoredRuntime, recoveryOverride, snapshotFromInspection,
  validateSnapshot } from './runtime-snapshot.mjs';

// 稳定的宿主命令合同，JSON 参数经 stdin 传入；不操作 Docker 或业务数据。
const operations = new Map([
  ['snapshot', snapshotFromInspection], ['validate', validateSnapshot], ['override', recoveryOverride],
  ['config', assertRecoveryConfig], ['restored', assertRestoredRuntime],
]);
try {
  if (process.argv.length !== 3 || !operations.has(process.argv[2])) throw new Error('未知运行合同操作');
  const args = JSON.parse(readFileSync(0, 'utf8'));
  if (!Array.isArray(args)) throw new Error('运行合同参数必须为数组');
  process.stdout.write(JSON.stringify(operations.get(process.argv[2])(...args) ?? null) + '\n');
} catch (error) {
  process.stderr.write(error.message + '\n');
  process.exitCode = 1;
}
