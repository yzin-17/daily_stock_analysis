// 已启用 HiThink 的目录资格检查；固定端点、有界两页，不改比赛和来源配置。
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { writeFileSync, mkdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
const args = process.argv.slice(2), value = (key) => { const i = args.indexOf(key); return i < 0 ? null : args[i + 1]; };
const day = value('--date'), output = resolve(value('--output') ?? '/tmp/hithink-history-catalog.json'), execute = args.includes('--execute');
assert(day && /^\d{4}-\d{2}-\d{2}$/.test(day), '须指定日期');
if (!execute) { console.log(JSON.stringify({ day, output, execute, maxPages: 2 })); process.exit(0); }
mkdirSync(dirname(output), { recursive: true });
writeFileSync(output + '.permit', JSON.stringify({ day }), { flag: 'wx', mode: 0o600 });
const inspect = () => JSON.parse(execFileSync('docker', ['inspect', 'thesis-ledger-dev-dsa-1'], { encoding: 'utf8' }))[0];
const target = inspect();
assert(target.State.Running && target.Config.Labels['com.docker.compose.project'] === 'thesis-ledger-dev' && target.Config.Labels['com.docker.compose.service'] === 'dsa', '目标 DSA 无效');
const result = JSON.parse(execFileSync('docker', ['exec', '-i', '--user', 'dsa:dsa', target.Id, 'python', '-B', '-m',
  'src.services.thesis_ledger_arena_catalog_probe'], { input: JSON.stringify({ day }), encoding: 'utf8', timeout: 30000, maxBuffer: 20_000_000 }));
const after = inspect();
assert(after.Id === target.Id && after.State.StartedAt === target.State.StartedAt, '检查期间 DSA 发生变化');
writeFileSync(output, JSON.stringify({ day, target: { id: target.Id, image: target.Image }, result }, null, 2) + '\n', { flag: 'wx', mode: 0o600 });
console.log(JSON.stringify({ ...result, raw: undefined, output }));
