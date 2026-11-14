import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { isAbsolute, join } from 'node:path';
import { pathToFileURL } from 'node:url';

const STOP = new Set(['rate_limited', 'rate_limited_global', 'quota_unavailable', 'quota_exhausted',
  'permission_denied', 'authentication_failed', 'credential_missing', 'not_configured',
  'permission_rejected', 'control_rejected']);
const identity = (entry) => JSON.stringify([entry.key, entry.target]);
const targetOf = (row) => Object.fromEntries(['providerId', 'upstreamSource'].filter((k) => k in row).map((k) => [k, row[k]]));
const sessionCode = `import {MarketSessionReader} from './apps/server/dist/src/market/market-session-reader.js';
import {MarketPeriodCalendarReader} from './apps/server/dist/src/market/market-period-calendar-reader.js';
import {DsaClient} from './apps/server/dist/src/integration/dsa/dsa.client.js';
const reader = new MarketSessionReader(new MarketPeriodCalendarReader(new DsaClient()));
const sessions = {}; for (const market of ['CN','HK','US']) sessions[market] = await reader.read(market);
console.log(JSON.stringify({observedAt:new Date().toISOString(),sessions}));`;

export function reconcile(history) {
  const latest = new Map();
  for (const name of ['asia-afternoon/asia-4.json', 'selected-retry/report.json',
    'selected-retry/report-akshare-startup-correction.json']) {
    const receipt = JSON.parse(readFileSync(join(history, name), 'utf8'));
    for (const row of (receipt.observation ?? receipt).observations) {
      const entry = { key: row.key, target: targetOf(row) };
      latest.set(identity(entry), { ...entry, status: row.status, errorCode: row.errorCode, evidence: name });
    }
  }
  const rows = [...latest.values()];
  const entries = rows.filter((r) => r.status === 'failed' && r.target.providerId !== 'tencent'
    && !r.target.upstreamSource?.startsWith('yahoo-')).map(({ key, target }) => ({ key, target }));
  validateEntries(entries);
  return { rows, entries };
}

export function validateEntries(entries) {
  assert(entries.length <= 12, '总预算超限');
  const seen = new Set();
  const counts = new Map();
  for (const entry of entries) {
    const { key, target } = entry;
    assert(['CN', 'HK'].includes(key.market) && key.assetType === 'STOCK' && key.adjustment === 'none'
      && ['1m', '60m', '1d'].includes(key.timeframe), '口径不在授权范围');
    assert(target.providerId !== 'tencent' && !target.upstreamSource?.startsWith('yahoo-'), '暂停或已知历史窗口不可请求');
    assert(!seen.has(identity(entry)), '精确口径重复');
    seen.add(identity(entry));
    const scope = `${target.providerId}:${key.market}`;
    counts.set(scope, (counts.get(scope) ?? 0) + 1);
    assert(counts.get(scope) <= 3, '来源市场预算超限');
  }
}

export async function runRecheck(plan, io = {}) {
  validateEntries(plan.entries);
  const now = io.now ?? Date.now;
  const run = io.run ?? ((command, args) => execFileSync(command, args,
    { encoding: 'utf8', maxBuffer: 2_000_000, timeout: 120_000 }));
  assert(isAbsolute(plan.output), '回执目录须为绝对路径');
  assert(!process.env.DOCKER_HOST, '不允许覆盖Docker端点');
  mkdirSync(plan.output, { recursive: true, mode: 0o700 });
  const save = (name, value) => writeFileSync(join(plan.output, name), JSON.stringify(value, null, 2) + '\n', { flag: 'wx', mode: 0o600 });
  assert(!existsSync(join(plan.output, 'execution.permit')), '已有执行许可，禁止重复执行');
  save('execution.permit', { actualStartedAt: new Date(now()).toISOString() });
  save('plan.json', plan);
  const inspect = () => {
    const [v] = JSON.parse(run('docker', ['inspect', 'thesis-ledger-dev-dsa-1']));
    assert(v.State.Running && v.Config.Labels['com.docker.compose.project'] === 'thesis-ledger-dev'
      && v.Config.Labels['com.docker.compose.service'] === 'dsa', 'DSA目标不匹配');
    return { id: v.Id, image: v.Image, startedAt: v.State.StartedAt };
  };
  const results = [];
  const stopped = new Set();
  const payload = readFileSync(new URL('./failed-provider-recheck.py', import.meta.url), 'utf8');
  for (const [index, entry] of plan.entries.entries()) {
    const scope = `${entry.target.providerId}:${entry.key.market}`;
    const base = { entry, actualStartedAt: new Date(now()).toISOString() };
    if (stopped.has(scope) || now() < Date.parse(plan.notBefore) || now() > Date.parse(plan.expiresAt)) {
      results.push({ ...base, status: 'skipped', reason: stopped.has(scope) ? 'source_stopped' : 'outside_window' });
      continue;
    }
    const session = JSON.parse(run('docker', ['exec', 'thesis-ledger-dev-thesis-ledger-1', 'node', '--input-type=module', '-e', sessionCode]));
    save(`session-${index + 1}.json`, session);
    if (session.sessions[entry.key.market]?.state !== 'open') {
      results.push({ ...base, status: 'skipped', reason: 'market_not_open' });
      continue;
    }
    if (now() > Date.parse(plan.expiresAt)) {
      results.push({ ...base, status: 'skipped', reason: 'outside_window' });
      continue;
    }
    const target = inspect();
    save(`query-${index + 1}.permit`, { ...base, target });
    let result;
    try {
      const observation = JSON.parse(run('docker', ['exec', '--user', 'dsa:dsa', target.id, 'python', '-B', '-c',
        payload, JSON.stringify(entry)]));
      assert(observation.readOnly === true && observation.observations.length <= 1, '回执合同错误');
      assert(JSON.stringify(target) === JSON.stringify(inspect()), '运行代次改变');
      result = { ...base, target, status: 'observed', observation };
      for (const row of observation.observations) if (STOP.has(row.errorCode)) stopped.add(scope);
    } catch (error) {
      result = { ...base, target, status: 'execution_unknown', errorType: error.name };
    }
    result.actualCompletedAt = new Date(now()).toISOString();
    result.entrySha256 = createHash('sha256').update(identity(entry)).digest('hex');
    save(`query-${index + 1}.json`, result);
    results.push(result);
    if (result.status === 'execution_unknown') break;
  }
  const report = { readOnly: true, actualCompletedAt: new Date(now()).toISOString(), results };
  save('report.json', report);
  return report;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const args = process.argv.slice(2);
  const execute = args.includes('--execute');
  const values = Object.fromEntries(args.filter((v) => v !== '--execute').map((v) => {
    const match = /^--(history|output|not-before|expires-at)=(.+)$/u.exec(v);
    assert(match, '未知选项'); return [match[1], match[2]];
  }));
  assert(isAbsolute(values.history) && isAbsolute(values.output), '路径须为绝对路径');
  assert(Number.isFinite(Date.parse(values['not-before'])) && Number.isFinite(Date.parse(values['expires-at'])), '窗口无效');
  const { entries, rows } = reconcile(values.history);
  if (!execute) {
    console.log(JSON.stringify({ readOnly: true, upstreamReads: 0, entries, offlineLatest: rows }));
  } else {
    const report = await runRecheck({ entries, offlineLatest: rows, output: values.output,
      notBefore: values['not-before'], expiresAt: values['expires-at'] });
    console.log(JSON.stringify({ evidence: values.output, results: report.results.map((r) => ({ status: r.status,
      provider: r.entry.target.providerId, timeframe: r.entry.key.timeframe,
      observation: r.observation?.observations.map((v) => ({ status: v.status, errorCode: v.errorCode })) })) }));
  }
}
