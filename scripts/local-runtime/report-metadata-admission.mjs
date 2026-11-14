import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdirSync, readFileSync, rmdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const project = 'thesis-ledger-dev';
export function reportAdmissionPlan(args) {
  const values = new Map();
  for (const arg of args) {
    const pair = /^(--confirm|--symbol|--start|--end|--sha256|--valid-hours|--recorded-by)=(.+)$/.exec(arg);
    const key = pair?.[1] ?? arg;
    assert(pair || ['--collect', '--apply', '--verify'].includes(arg), '未知或缺值选项');
    assert(!values.has(key), '重复选项');
    values.set(key, pair?.[2] ?? true);
  }
  const symbol = values.get('--symbol');
  assert.match(symbol ?? '', /^[0-9]{6}\.(?:SH|SZ)$/, '须指定沪深股票规范代码');
  const start = values.get('--start'), end = values.get('--end');
  for (const date of [start, end]) {
    assert.match(date ?? '', /^\d{4}-\d{2}-\d{2}$/);
    assert.equal(new Date(date).toISOString().slice(0, 10), date, '日期无效');
  }
  assert(start <= end, '窗口倒序');
  assert.equal(values.get('--confirm'), `${project}/akshare/eastmoney/${symbol}/${start}/${end}`, '目标确认不匹配');
  const modes = ['collect', 'apply', 'verify'].filter(mode => values.has(`--${mode}`));
  assert(modes.length <= 1, '执行模式互斥');
  const mode = modes[0] ?? 'plan';
  const hours = values.get('--valid-hours') ?? '24';
  assert.match(hours, /^(?:[1-9]|1[0-9]|2[0-4])$/, '有效期须为1～24整数小时');
  const recordedBy = values.get('--recorded-by') ?? null;
  if (recordedBy !== null) assert.match(recordedBy, /^[A-Za-z0-9][A-Za-z0-9_.@/-]{0,63}$/);
  const digest = values.get('--sha256') ?? null;
  if (digest !== null) assert.match(digest, /^[a-f0-9]{64}$/);
  if (['apply', 'verify'].includes(mode)) assert(digest && recordedBy && values.has('--valid-hours'), '应用与核验须明确证据摘要、记录人及有效期');
  if (mode === 'collect') assert(digest === null, '采证不接受已有摘要');
  return { mode, project, symbol, start, end, digest, validHours: Number(hours), recordedBy };
}

export function reportTargetIdentity(value) {
  assert.equal(value.Name, `/${project}-dsa-1`);
  assert.equal(value.Config.Labels['com.docker.compose.project'], project);
  assert.equal(value.Config.Labels['com.docker.compose.service'], 'dsa');
  assert(value.State.Running && value.State.Health?.Status === 'healthy', '目标DSA未健康运行');
  assert.match(value.Id, /^[a-f0-9]{64}$/);
  assert.match(value.Image, /^sha256:[a-f0-9]{64}$/);
  assert(value.Mounts.some(m => m.Type === 'volume' && m.Name === 'thesis-ledger-dsa-data' && m.Destination === '/app/data' && m.RW));
  const mounts = value.Mounts.map(({ Type, Name, Source, Destination, RW }) => ({ Type, Name, Source, Destination, RW }))
    .sort((a, b) => a.Destination.localeCompare(b.Destination));
  return { id: value.Id, image: value.Image, mounts };
}

export function main(args = process.argv.slice(2)) {
  const plan = reportAdmissionPlan(args);
  if (plan.mode === 'plan') return { ...plan, page: 1, pageSize: 2, requestBudgetMs: 3000,
    target: { providerId: 'akshare', upstreamSource: 'eastmoney' }, capability: 'RESEARCH_REPORT_METADATA',
    existingAdmission: '必须不存在；不覆盖或续期', writes: false };
  assert(!process.env.DOCKER_HOST && !process.env.BUILDX_BUILDER, '不允许覆盖Docker端点');
  const run = (cmd, argv, input) => execFileSync(cmd, argv, { input, encoding: 'utf8', timeout: 30_000, maxBuffer: 1_000_000 }).trim();
  const docker = (...argv) => run('docker', argv);
  assert.equal(docker('context', 'show'), 'desktop-linux');
  const endpoint = docker('context', 'inspect', 'desktop-linux', '--format', '{{.Endpoints.docker.Host}}');
  assert(endpoint.startsWith('unix://'), '仅允许本地Unix端点');
  const checksum = run('cksum', [], endpoint).split(/\s+/)[0];
  const lock = join(tmpdir(), `thesis-ledger-update-${process.getuid()}-${checksum}.lock`);
  mkdirSync(lock);
  try {
    const name = `${project}-dsa-1`;
    const identity = reportTargetIdentity(JSON.parse(docker('inspect', name))[0]);
    const worker = readFileSync(new URL('./report-metadata-admission.py', import.meta.url), 'utf8');
    assert.deepEqual(reportTargetIdentity(JSON.parse(docker('inspect', name))[0]), identity, '执行前容器身份变化');
    const receipt = JSON.parse(run('docker', ['exec', '-i', '--user', 'dsa:dsa', identity.id, 'python', '-B', '-c', worker], JSON.stringify(plan)));
    assert.deepEqual(reportTargetIdentity(JSON.parse(docker('inspect', name))[0]), identity, '执行后容器身份变化；须读回，不自动重试');
    return receipt;
  } finally { rmdirSync(lock); }
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) console.log(JSON.stringify(main(), null, 2));
