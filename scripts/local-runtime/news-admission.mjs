import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdirSync, readFileSync, rmdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { isAbsolute, join } from 'node:path';
import { pathToFileURL } from 'node:url';

const project = 'thesis-ledger-dev';
export function newsAdmissionPlan(args, read = readFileSync) {
  const sourceOption = args.filter(arg => arg.startsWith('--source='));
  assert.equal(sourceOption.length, 1, '必须显式指定且仅指定一个 --source');
  const source = sourceOption[0].slice('--source='.length);
  const profiles = {
    'akshare-eastmoney': { providerId: 'akshare', symbol: '600519.SH', license: 'public-metadata-research' },
    'hithink-timeline': { providerId: 'hithink', symbol: '600519.SH', license: 'account-news-metadata' },
    'yahoo-search': { providerId: 'yfinance', symbol: 'NVDA.US', license: 'public-metadata-research' },
    'cls-roll': { providerId: 'cls', symbol: 'CN.STOCK', license: 'public-metadata-research' },
    'wallstreetcn-a-stock': { providerId: 'wallstreetcn', symbol: 'CN.STOCK', license: 'public-metadata-research' },
  };
  assert(Object.hasOwn(profiles, source), '未审阅的新闻来源');
  const profile = profiles[source];
  const values = new Map();
  for (const arg of args.filter(arg => !arg.startsWith('--source='))) {
    const pair = /^(--confirm|--feed|--sha256|--observed-at|--start|--end|--valid-hours|--recorded-by|--expected-record-version|--expected-record-sha256)=(.+)$/.exec(arg);
    const key = pair?.[1] ?? arg;
    assert(pair || ['--apply', '--verify', '--extend-existing', '--renew-existing', '--roll-recent-window'].includes(arg), '未知或缺值选项');
    assert(!values.has(key), '重复选项'); values.set(key, pair?.[2] ?? true);
  }
  assert.equal(values.get('--confirm'), `${project}/${profile.providerId}/${profile.symbol}/${profile.license}`);
  assert(isAbsolute(values.get('--feed') ?? ''), '来源证据须为绝对路径');
  const digest = values.get('--sha256'); assert.match(digest ?? '', /^[a-f0-9]{64}$/);
  const extendExisting = values.has('--extend-existing');
  const renewExisting = values.has('--renew-existing');
  const rollRecentWindow = values.has('--roll-recent-window');
  assert(!(extendExisting && renewExisting), '扩展与续验互斥');
  assert(!rollRecentWindow || (renewExisting && source === 'hithink-timeline'), '滚动窗口仅限显式同花顺过期续验');
  const validHours = extendExisting ? null : Number(values.get('--valid-hours'));
  if (extendExisting || renewExisting) {
    if (renewExisting) {
      assert.equal(source, 'hithink-timeline', '续验仅限原同花顺个股新闻');
      assert.equal(validHours, 1, '续验仅允许一小时');
    } else {
      assert(['akshare-eastmoney', 'hithink-timeline'].includes(source), '未授权扩展的新闻来源');
      assert(!values.has('--valid-hours'), '范围扩展不能另设或延长有效期');
    }
    assert(Number.isSafeInteger(Number(values.get('--expected-record-version'))) && Number(values.get('--expected-record-version')) >= 1, '必须确认旧记录版本');
    assert.match(values.get('--expected-record-sha256') ?? '', /^[a-f0-9]{64}$/, '必须确认旧记录摘要');
  } else {
    assert(!values.has('--expected-record-version') && !values.has('--expected-record-sha256'), '旧记录确认只用于显式范围扩展');
    assert(Number.isInteger(validHours) && validHours >= 1 && validHours <= 168, '有效期必须为1～168整数小时');
  }
  const recordedBy = values.get('--recorded-by'); assert.match(recordedBy ?? '', /^[A-Za-z0-9][A-Za-z0-9_.@/-]{0,63}$/);
  for (const key of ['--start', '--end']) assert.match(values.get(key) ?? '', /^\d{4}-\d{2}-\d{2}$/);
  const observedAt = values.get('--observed-at'); assert.match(observedAt ?? '', /^\d{4}-\d\d-\d\dT.+(?:Z|[+-]\d\d:\d\d)$/);
  assert(Number.isFinite(Date.parse(observedAt)));
  const raw = read(values.get('--feed')); assert(Buffer.isBuffer(raw) && raw.length > 0 && raw.length <= 512 * 1024);
  assert.equal(createHash('sha256').update(raw).digest('hex'), digest, '订阅证据摘要变化');
  assert(!(values.has('--apply') && values.has('--verify')), '应用与只读核验不能同时指定');
  let mode = 'plan';
  if (values.has('--apply')) mode = 'apply';
  if (values.has('--verify')) mode = 'verify';
  return { mode, project, providerId: profile.providerId, symbol: profile.symbol,
    start: values.get('--start'), end: values.get('--end'), validHours, recordedBy, observedAt,
    feedSha256: digest, feedBase64: raw.toString('base64'), license: profile.license,
    ...(rollRecentWindow ? { rollRecentWindow: true } : {}),
    ...(extendExisting || renewExisting ? { ...(renewExisting ? { renewExisting: true } : { extendExisting: true }), expectedRecordVersion: Number(values.get('--expected-record-version')),
      expectedRecordSha256: values.get('--expected-record-sha256') } : {}) };
}

export function newsAdmissionWorker(read = readFileSync) {
  const helper = read(new URL('./news_admission_extension.py', import.meta.url), 'utf8');
  const renewal = read(new URL('./news_admission_renewal.py', import.meta.url), 'utf8');
  const worker = read(new URL('./news-admission.py', import.meta.url), 'utf8');
  return `import sys, types\n_extension = types.ModuleType('news_admission_extension')\nexec(${JSON.stringify(helper)}, _extension.__dict__)\nsys.modules['news_admission_extension'] = _extension\n_renewal = types.ModuleType('news_admission_renewal')\nexec(${JSON.stringify(renewal)}, _renewal.__dict__)\nsys.modules['news_admission_renewal'] = _renewal\n${worker}`;
}

export function newsTargetIdentity(value) {
  assert.equal(value.Name, `/${project}-dsa-1`);
  assert.equal(value.Config.Labels['com.docker.compose.project'], project);
  assert.equal(value.Config.Labels['com.docker.compose.service'], 'dsa');
  assert(value.State.Running && value.State.Health?.Status === 'healthy');
  assert.match(value.Id, /^[a-f0-9]{64}$/); assert.match(value.Image, /^sha256:[a-f0-9]{64}$/);
  assert(value.Mounts.some(m => m.Type === 'volume' && m.Name === 'thesis-ledger-dsa-data' && m.Destination === '/app/data' && m.RW));
  const mounts = value.Mounts.map(({ Type, Name, Source, Destination, RW }) => ({ Type, Name, Source, Destination, RW }))
    .sort((a, b) => a.Destination.localeCompare(b.Destination));
  return { id: value.Id, image: value.Image, mounts };
}

export function main(args = process.argv.slice(2)) {
  const plan = newsAdmissionPlan(args);
  const { feedBase64, ...receipt } = plan;
  if (plan.mode === 'plan') return { ...receipt, writes: false };
  return executeNewsWorker(plan, newsAdmissionWorker());
}

export function executeNewsWorker(plan, worker) {
  assert(!process.env.DOCKER_HOST && !process.env.BUILDX_BUILDER, '不允许覆盖Docker端点');
  const run = (cmd, argv, input) => execFileSync(cmd, argv, { input, encoding: 'utf8', timeout: 30_000, maxBuffer: 1_200_000 }).trim();
  const docker = (...argv) => run('docker', argv);
  assert.equal(docker('context', 'show'), 'desktop-linux');
  const endpoint = docker('context', 'inspect', 'desktop-linux', '--format', '{{.Endpoints.docker.Host}}');
  assert(endpoint.startsWith('unix://'));
  const checksum = run('cksum', [], endpoint).split(/\s+/)[0];
  const lock = join(tmpdir(), `thesis-ledger-update-${process.getuid()}-${checksum}.lock`);
  mkdirSync(lock);
  try {
    const name = `${project}-dsa-1`;
    const identity = newsTargetIdentity(JSON.parse(docker('inspect', name))[0]);
    assert.deepEqual(newsTargetIdentity(JSON.parse(docker('inspect', name))[0]), identity);
    const result = JSON.parse(run('docker', ['exec', '-i', '--user', 'dsa:dsa', identity.id, 'python', '-B', '-c', worker], JSON.stringify(plan)));
    assert.deepEqual(newsTargetIdentity(JSON.parse(docker('inspect', name))[0]), identity, '执行后身份变化，须读回且不自动重试');
    return result;
  } finally { rmdirSync(lock); }
}
if (import.meta.url === pathToFileURL(process.argv[1]).href) console.log(JSON.stringify(main(), null, 2));
