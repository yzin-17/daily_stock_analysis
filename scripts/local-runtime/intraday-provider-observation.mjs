import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdirSync, readFileSync, writeFileSync, existsSync } from 'node:fs';
import { isAbsolute, join } from 'node:path';
import { pathToFileURL } from 'node:url';

export const observationSchedules = {
  asia: { markets: ['CN', 'HK'], slots: ['2026-11-11T10:05:00+08:00', '2026-11-11T10:15:00+08:00',
    '2026-11-11T10:35:00+08:00', '2026-11-11T11:05:00+08:00'] },
  us: { markets: ['US'], slots: ['2026-11-11T23:05:00+08:00', '2026-11-11T23:15:00+08:00',
    '2026-11-11T23:35:00+08:00', '2026-11-12T00:05:00+08:00'] },
};

export function observationPlan(args) {
  const values = new Map();
  for (const arg of args) {
    const match = /^--(phase|output)=(.+)$/u.exec(arg);
    assert(match || arg === '--execute', '未知选项');
    const key = match?.[1] ?? 'execute';
    assert(!values.has(key), '重复选项');
    values.set(key, match?.[2] ?? true);
  }
  const phase = values.get('phase');
  const output = values.get('output');
  assert(Object.hasOwn(observationSchedules, phase), '采样阶段无效');
  assert(typeof output === 'string' && isAbsolute(output), '输出目录须为绝对路径');
  return { phase, output, execute: values.has('execute'), ...observationSchedules[phase] };
}

function targetIdentity(run) {
  const [value] = JSON.parse(run('docker', ['inspect', 'thesis-ledger-dev-dsa-1']));
  assert(value.State?.Running === true, 'DSA 未运行');
  assert(value.Config?.Labels?.['com.docker.compose.service'] === 'dsa', '目标不是 DSA');
  assert(value.Config?.Labels?.['com.docker.compose.project'] === 'thesis-ledger-dev', '项目不匹配');
  return { id: value.Id, image: value.Image, startedAt: value.State.StartedAt };
}

export async function runObservation(plan, io = {}) {
  if (!plan.execute) return { ...plan, readOnly: true, maxLogicalReadsPerProviderMarket: 12 };
  const now = io.now ?? Date.now;
  const wait = io.wait ?? ((ms) => new Promise((resolve) => setTimeout(resolve, ms)));
  const run = io.run ?? ((command, args, input) => execFileSync(command, args,
    { input, encoding: 'utf8', maxBuffer: 4_000_000, timeout: 600_000 }));
  assert(now() < Date.parse(plan.slots.at(-1)) + 15 * 60_000, '采样日期或窗口已过，禁止改为次日执行');
  assert(!process.env.DOCKER_HOST, '不允许覆盖 Docker 端点');
  mkdirSync(plan.output, { recursive: true, mode: 0o700 });
  let previous = null;
  const results = [];
  for (const [index, scheduledAt] of plan.slots.entries()) {
    while (now() < Date.parse(scheduledAt)) await wait(Math.min(60_000, Date.parse(scheduledAt) - now()));
    const output = join(plan.output, `${plan.phase}-${index + 1}.json`);
    const permit = `${output}.permit`;
    if (existsSync(output)) {
      const existing = JSON.parse(readFileSync(output, 'utf8'));
      assert(existing.phase === plan.phase && existing.scheduledAt === scheduledAt, '已有回执不匹配');
      if (existing.observation) previous = existing.observation;
      results.push(existing);
      if (existing.status === 'execution_unknown') break;
      continue;
    }
    assert(!existsSync(permit), '已有请求许可但无回执，结果未知，禁止自动重试');
    const base = { phase: plan.phase, scheduledAt, actualStartedAt: new Date(now()).toISOString() };
    if (now() > Date.parse(scheduledAt) + 15 * 60_000) {
      const missed = { ...base, status: 'missed', attempted: false };
      writeFileSync(output, JSON.stringify(missed, null, 2) + '\n', { flag: 'wx', mode: 0o600 });
      results.push(missed);
      continue;
    }
    writeFileSync(permit, JSON.stringify(base), { flag: 'wx', mode: 0o600 });
    let identity;
    let result;
    try {
      identity = targetIdentity(run);
      const args = ['exec', '-i', '--user', 'dsa:dsa', identity.id, 'python', '-B', '-m',
        'src.services.thesis_ledger_intraday_observation', '--markets', ...plan.markets];
      if (previous) args.push('--previous-stdin');
      const observation = JSON.parse(run('docker', args, previous ? JSON.stringify(previous) : undefined));
      assert(observation.readOnly === true && Array.isArray(observation.observations), '缺少有效采样回执');
      assert(JSON.stringify(identity) === JSON.stringify(targetIdentity(run)), '读取期间容器发生变化');
      previous = observation;
      result = { ...base, status: 'observed', target: identity, observation };
    } catch (error) {
      result = { ...base, status: 'execution_unknown', errorType: error.name, target: identity };
    }
    writeFileSync(output, JSON.stringify(result, null, 2) + '\n', { flag: 'wx', mode: 0o600 });
    results.push(result);
    if (result.status !== 'observed') break;
    io.onBatch?.({ phase: plan.phase, batch: index + 1, output });
  }
  return { phase: plan.phase, outputs: results.length, finished: results.length === plan.slots.length,
    evidence: plan.output, statuses: results.map((item) => item.status) };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const result = await runObservation(observationPlan(process.argv.slice(2)), {
    onBatch: (value) => console.log(JSON.stringify(value)),
  });
  console.log(JSON.stringify(result));
}
