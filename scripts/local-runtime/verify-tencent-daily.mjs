import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { isAbsolute, join } from 'node:path';
import { pathToFileURL } from 'node:url';

export function verificationPlan(args) {
  const values = new Map();
  for (const arg of args) {
    const match = /^--(date|output)=(.+)$/u.exec(arg);
    assert(match || arg === '--execute', '未知选项');
    const key = match?.[1] ?? 'execute';
    assert(!values.has(key), '重复选项');
    values.set(key, match?.[2] ?? true);
  }
  const day = values.get('date');
  assert(/^\d{4}-\d{2}-\d{2}$/u.test(day ?? '') && new Date(day).toISOString().slice(0, 10) === day, '日期无效');
  const output = values.get('output');
  assert(typeof output === 'string' && isAbsolute(output), '输出路径须为绝对路径');
  return { day, output, execute: values.has('execute'), symbol: '600519.SH', timeframe: '1d', adjustment: 'none' };
}

export function runVerification(plan, io = {}) {
  if (!plan.execute) return { ...plan, readOnly: true, providerReads: 0, plannedLogicalReads: 1, costUSD: 0 };
  assert(!process.env.DOCKER_HOST, '不允许覆盖Docker端点');
  const run = io.run ?? ((command, args, input) => execFileSync(command, args,
    { input, encoding: 'utf8', maxBuffer: 2_000_000, timeout: 60_000 }));
  mkdirSync(plan.output, { recursive: true, mode: 0o700 });
  assert(!existsSync(join(plan.output, 'execution.permit')), '已有许可，禁止自动重发');
  const save = (name, value) => writeFileSync(join(plan.output, name), JSON.stringify(value, null, 2) + '\n', { flag: 'wx', mode: 0o600 });
  save('execution.permit', { plan, actualStartedAt: new Date().toISOString() });
  const inspect = () => {
    const [v] = JSON.parse(run('docker', ['inspect', 'thesis-ledger-dev-dsa-1']));
    assert(v.State.Running && v.Config.Labels['com.docker.compose.project'] === 'thesis-ledger-dev'
      && v.Config.Labels['com.docker.compose.service'] === 'dsa', 'DSA目标不匹配');
    return { id: v.Id, image: v.Image, startedAt: v.State.StartedAt };
  };
  let result;
  try {
    const sessionCode = `import {MarketSessionReader} from './apps/server/dist/src/market/market-session-reader.js';
import {MarketPeriodCalendarReader} from './apps/server/dist/src/market/market-period-calendar-reader.js';
import {DsaClient} from './apps/server/dist/src/integration/dsa/dsa.client.js';
console.log(JSON.stringify(await new MarketSessionReader(new MarketPeriodCalendarReader(new DsaClient())).read('CN')));`;
    const session = JSON.parse(run('docker', ['exec', 'thesis-ledger-dev-thesis-ledger-1', 'node', '--input-type=module', '-e', sessionCode]));
    save('session.json', session);
    assert(session.date === plan.day && session.tradingDayState === 'open'
      && ['open', 'closed'].includes(session.state), '当日交易日事实未知或不满足条件');
    const target = inspect();
    const nativeSha256 = createHash('sha256').update(readFileSync(new URL('../../data_provider/tencent_native_daily.py', import.meta.url))).digest('hex');
    const payload = readFileSync(new URL('./verify-tencent-daily.py', import.meta.url), 'utf8');
    const observation = JSON.parse(run('docker', ['exec', '-i', '--user', 'dsa:dsa', target.id, 'python', '-B', '-c', payload],
      JSON.stringify({ ...plan, nativeSha256 })));
    assert(observation.readOnly === true, '缺少只读回执');
    const targetUnchanged = JSON.stringify(target) === JSON.stringify(inspect());
    result = { plan, session, target, targetUnchanged, observation, actualCompletedAt: new Date().toISOString() };
    assert(targetUnchanged, '目标运行代次改变');
  } catch (error) {
    result = { ...result, status: 'execution_unknown', errorType: error.name, actualCompletedAt: new Date().toISOString() };
  }
  save('report.json', result);
  return { status: result.status ?? result.observation.status, providerReads: result.observation?.requestCount ?? null,
    evidence: plan.output, targetUnchanged: result.targetUnchanged ?? null };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href)
  console.log(JSON.stringify(runVerification(verificationPlan(process.argv.slice(2)))));
