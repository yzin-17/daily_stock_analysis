import assert from 'node:assert/strict';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve, sep } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { assertDsaTarget } from '../../../daily-stock-analysis/scripts/local-runtime/dsa-target-contract.mjs';
import { digest, docker, inspect } from '../../../thesis-ledger-infra/scripts/runtime-io.mjs';

// 专用验收入口：只在已运行 DSA 的 /tmp 写合成 SQLite，不修改正式配置或数据。
const infra = resolve(dirname(fileURLToPath(import.meta.url)), '../../../thesis-ledger-infra');
export function accountLevelReportPath(args) {
assert.equal(args.length, 1, '需要一个报告路径');
const [output] = args;
assert(output && resolve(output) === output && !existsSync(output), '报告必须为新的绝对路径');
assert(output.startsWith(join(infra, '.maintenance-backups') + sep), '报告必须位于私有维护目录');
return output;
}

export function runAcceptance(args, io = {}) {
const output = accountLevelReportPath(args);
const readTarget = io.inspect ?? inspect;
const execute = io.execute ?? ((binary, args, options) => {
  assert.equal(binary, 'docker');
  try { return { status: 0, stdout: docker(args, options) }; }
  catch (error) { return { status: error.code, stdout: error.stdout, stderr: error.stderr }; }
});
const save = io.write ?? writeFileSync;
const before = readTarget('thesis-ledger-dev-dsa-1');
assertDsaTarget(before);
const program = readFileSync(new URL('./tushare-account-level-acceptance.py', import.meta.url), 'utf8');
const result = execute('docker', ['exec', '-i', '--user', '1000:1000', '--workdir', '/app',
  before.Id, 'python', '-B', '-'], { input: program, encoding: 'utf8', timeout: 120000,
  maxBuffer: 2 * 1024 * 1024 });
let observed;
try { observed = JSON.parse(result.stdout); } catch { throw new Error('目标未返回有效验收报告'); }
assert.equal(result.status, 0, observed.reason ?? '目标隔离验收失败');
assert.equal(observed.passed, true);
assert.equal(observed.cleaned, true);
assert.equal(observed.realProviderRequests, 0);
const after = readTarget('thesis-ledger-dev-dsa-1');
assertDsaTarget(after);
assert.equal(after.Id, before.Id, '验收期间容器被替换');
assert.equal(after.State.StartedAt, before.State.StartedAt, '验收期间容器重启');
assert.equal(after.Image, before.Image);
const report = { version: 'tushare-account-level-target-v1', checkedAt: new Date().toISOString(),
  image: before.Image, programSha256: digest(program), ...observed };
save(output, JSON.stringify(report, null, 2) + '\n', { flag: 'wx', mode: 0o600 });
return { path: output, image: report.image, passed: true,
  restoredPoints: observed.phases[1].points, restoredInterfaces: observed.phases[1].states.length,
  restoreInterfaceCalls: observed.phases[1].calls.length, realProviderRequests: 0, cleaned: true };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  console.log(JSON.stringify(runAcceptance(process.argv.slice(2))));
}
