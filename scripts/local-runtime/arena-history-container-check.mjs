// 目标 DSA 中的有界只读历史目录检查；SDK 子进程由已有 runner 终止并回收。
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';

const args = process.argv.slice(2), execute = args.includes('--execute');
const value = (key) => { const i = args.indexOf(key); return i < 0 ? null : args[i + 1]; };
const day = value('--date'), output = resolve(value('--output') ?? '/tmp/arena-history-container-check.json');
assert(day && /^\d{4}-\d{2}-\d{2}$/.test(day), '需要 --date');
const plan = { providerId: 'baostock', day, maxQueries: 2, timeoutSeconds: 45, execute };
if (!execute) { console.log(JSON.stringify(plan)); process.exit(0); }
const providers = await (await fetch('http://127.0.0.1:3000/api/market-data/providers', { signal: AbortSignal.timeout(5000) })).json();
assert(providers.providers.some((p) => p.providerId === 'baostock' && p.enabled), 'BaoStock 未启用');
mkdirSync(dirname(output), { recursive: true });
writeFileSync(output + '.permit', JSON.stringify(plan), { flag: 'wx', mode: 0o600 });
const inspect = () => JSON.parse(execFileSync('docker', ['inspect', 'thesis-ledger-dev-dsa-1'], { encoding: 'utf8' }))[0];
const target = inspect();
assert(target.State.Running && target.Config.Labels['com.docker.compose.project'] === 'thesis-ledger-dev' && target.Config.Labels['com.docker.compose.service'] === 'dsa', '目标 DSA 身份无效');
const code = 'import json,sys; from data_provider.baostock_query_process import read_catalog; p=json.load(sys.stdin); print(json.dumps(read_catalog(p["day"]),ensure_ascii=False))';
let result;
try {
  const raw = JSON.parse(execFileSync('docker', ['exec', '-i', '--user', 'dsa:dsa', target.Id, 'python', '-B', '-c', code], {
    input: JSON.stringify({ day }), encoding: 'utf8', maxBuffer: 20_000_000, timeout: 60000, stdio: ['pipe', 'pipe', 'pipe'],
  }));
  writeFileSync(output + '.raw.json', JSON.stringify(raw), { flag: 'wx', mode: 0o600 });
  const basics = new Map(raw.basics.map((b) => [b.code, b]));
  const exchanges = {}, types = {};
  let missingTypes = 0;
  for (const row of raw.members) {
    const exchange = row.code.split('.')[0]; exchanges[exchange] = (exchanges[exchange] ?? 0) + 1;
    const basic = basics.get(row.code);
    if (!basic) missingTypes++;
    else types[basic.type] = (types[basic.type] ?? 0) + 1;
  }
  result = { status: 'observed', members: raw.members.length, basics: raw.basics.length, exchanges, types, missingTypes };
} catch (error) {
  const details = error.stderr?.toString() ?? '';
  result = { status: 'unavailable', reason: details.includes('超时') ? 'timeout' : 'source_query_failed' };
}
const after = inspect();
assert(after.Id === target.Id && after.State.StartedAt === target.State.StartedAt, '检查期间目标发生变化');
const receipt = { ...plan, target: { id: target.Id, image: target.Image }, result, observedAt: new Date().toISOString() };
writeFileSync(output, JSON.stringify(receipt, null, 2) + '\n', { flag: 'wx', mode: 0o600 });
console.log(JSON.stringify(receipt));
