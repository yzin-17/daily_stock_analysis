import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdirSync, readFileSync, rmdirSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { isAbsolute, join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { reportTargetIdentity } from './report-metadata-admission.mjs';

export const sourceCases = ['report-eps', 'sina-fzb', 'sina-lrb', 'sina-llb', 'valuation', 'lpr', 'global-news', 'fund-flow', 'tencent-ticks',
  'margin', 'holder-count', 'block-trade', 'earnings-forecast', 'institution-survey', 'holder-trades', 'buyback', 'dragon-tiger', 'concepts', 'industry-ranking', 'news-original',
  'consensus-eps', 'earnings-express', 'macro-gdp', 'macro-pmi', 'index-members', 'tencent-date', 'macro-calendar',
  'limit-up', 'limit-break', 'limit-down', 'yesterday-limit', 'stock-monitor', 'bond-yields'];

export function sourceCheckPlan(args) {
  const values = new Map();
  for (const arg of args) {
    const pair = /^--(case|symbol|output|start|end|attempt)=(.+)$/u.exec(arg);
    assert(pair || arg === '--execute', '未知或缺值选项');
    const key = pair?.[1] ?? 'execute';
    assert(!values.has(key), '重复选项');
    values.set(key, pair?.[2] ?? true);
  }
  const profile = values.get('case');
  assert(sourceCases.includes(profile), '来源样本类型无效');
  const symbol = values.get('symbol');
  assert.match(symbol ?? '', /^\d{6}\.(?:SH|SZ)$/u, '需要规范沪深证券代码');
  const output = values.get('output');
  assert(output && isAbsolute(output), '证据目录必须为绝对路径');
  const attempt = values.get('attempt') ?? '1';
  assert.match(attempt, /^[1-9][0-9]*$/u, '尝试序号无效');
  const start = values.get('start') ?? '2026-09-21';
  const end = values.get('end') ?? '2026-09-25';
  for (const date of [start, end]) {
    assert.match(date, /^\d{4}-\d{2}-\d{2}$/u);
    assert.equal(new Date(date).toISOString().slice(0, 10), date, '日期无效');
  }
  assert(start <= end, '日期窗口倒序');
  return { profile, symbol, start, end, attempt: Number(attempt), output: resolve(output), execute: values.has('execute') };
}

export function runSourceCheck(plan) {
  if (!plan.execute) {
    let providerRequests = 1;
    if (plan.profile === 'valuation') providerRequests = '一个既有 SDK 会话及查询';
    else if (plan.profile === 'news-original') providerRequests = 2;
    return { ...plan, providerRequests, writesConfiguration: false, modelCalls: 0, newCostUSD: 0 };
  }
  const run = (command, args, input) => execFileSync(command, args,
    { input, encoding: 'utf8', maxBuffer: 8_000_000, timeout: 90_000 }).trim();
  const docker = (...args) => run('docker', args);
  assert(!process.env.DOCKER_HOST && !process.env.BUILDX_BUILDER, '不允许覆盖 Docker 端点');
  assert.equal(docker('context', 'show'), 'desktop-linux');
  const endpoint = docker('context', 'inspect', 'desktop-linux', '--format', '{{.Endpoints.docker.Host}}');
  assert(endpoint.startsWith('unix://'), '只允许本地 Unix 端点');
  const checksum = run('cksum', [], endpoint).split(/\s+/u)[0];
  const lock = join(tmpdir(), `thesis-ledger-update-${process.getuid()}-${checksum}.lock`);
  mkdirSync(plan.output, { recursive: true, mode: 0o700 });
  const path = join(plan.output, `${plan.profile}-${plan.attempt}.json`);
  const permit = join(plan.output, `${plan.profile}-${plan.attempt}.permit`);
  writeFileSync(permit, JSON.stringify({ at: new Date().toISOString(), plan }), { flag: 'wx', mode: 0o600 });
  mkdirSync(lock);
  try {
    const identity = reportTargetIdentity(JSON.parse(docker('inspect', 'thesis-ledger-dev-dsa-1'))[0]);
    const worker = readFileSync(new URL('./astock-existing-source-check.py', import.meta.url), 'utf8');
    const input = { profile: plan.profile, symbol: plan.symbol, start: plan.start, end: plan.end };
    let stdout;
    try {
      stdout = run('docker', ['exec', '-i', '--user', 'dsa:dsa', identity.id, 'python', '-B', '-c', worker], JSON.stringify(input));
    } catch (error) {
      const unknown = { profile: plan.profile, outcome: 'execution-unknown', errorName: error.code ?? error.name,
        attemptedAt: new Date().toISOString(), target: identity };
      writeFileSync(path, JSON.stringify(unknown, null, 2) + '\n', { flag: 'wx', mode: 0o600 });
      throw error;
    }
    const line = stdout.split('\n').findLast(value => value.startsWith('ASTOCK_SOURCE_RESULT '));
    assert(line, '缺少来源回执；不能自动重试');
    const receipt = JSON.parse(line.slice('ASTOCK_SOURCE_RESULT '.length));
    const targetUnchanged = JSON.stringify(reportTargetIdentity(JSON.parse(docker('inspect', 'thesis-ledger-dev-dsa-1'))[0])) === JSON.stringify(identity);
    const result = { ...receipt, target: identity, targetUnchanged, attempt: plan.attempt, recordedAt: new Date().toISOString() };
    writeFileSync(path, JSON.stringify(result, null, 2) + '\n', { flag: 'wx', mode: 0o600 });
    assert(targetUnchanged && receipt.protectionPassed, '目标或原配置保护未通过');
    return { profile: plan.profile, outcome: receipt.outcome, outbound: receipt.requests?.length ?? 0,
      httpStatus: receipt.response?.httpStatus ?? null, path, protectionPassed: receipt.protectionPassed };
  } finally {
    rmdirSync(lock);
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href)
  console.log(JSON.stringify(runSourceCheck(sourceCheckPlan(process.argv.slice(2)))));
