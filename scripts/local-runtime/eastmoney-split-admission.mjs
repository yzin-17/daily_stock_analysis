import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdirSync, readFileSync, rmdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

export const reviewedDigest = 'b1a4f03f94faf27acf95d2bef9d508ec06eabec1280e04c921bb677154f5fe8e';
const project = 'thesis-ledger-dev';
const confirmation = `${project}/159516.SZ/2026-03-30/2026-07-10`;
const mappingUrl = new URL('../../../thesis-ledger/docs/evidence/2026-10-02-m22-159516-split-mapping.json', import.meta.url);
const workerUrl = new URL('./eastmoney-split-admission.py', import.meta.url);

export function parseSplitAdmissionArguments(args) {
  const values = new Map();
  for (const arg of args) {
    const match = /^(--confirm|--sha256|--valid-hours|--recorded-by)=(.+)$/.exec(arg);
    const key = match?.[1] ?? arg;
    assert(match || arg === '--apply', '未知或缺值选项');
    assert(!values.has(key), '重复选项');
    values.set(key, match?.[2] ?? true);
  }
  assert.equal(values.get('--confirm'), confirmation, '需确认原精确项目/证券/窗口');
  assert.equal(values.get('--sha256'), reviewedDigest, '需确认原映射字节摘要');
  const hours = values.get('--valid-hours') ?? '24';
  assert.match(hours, /^(?:[1-9]|1[0-9]|2[0-4])$/, '有效期必须为1～24整数小时');
  const recordedBy = values.get('--recorded-by') ?? null;
  if (recordedBy !== null) assert.match(recordedBy, /^[A-Za-z0-9][A-Za-z0-9_.@/-]{0,63}$/, '记录人必须为明确审阅标识');
  const apply = values.has('--apply');
  if (apply) assert(values.has('--valid-hours') && recordedBy !== null, '应用前必须显式指定批准的有效期和记录人');
  return { apply, validHours: Number(hours), recordedBy };
}

export function splitAdmissionPlan(options, content) {
  assert(Buffer.isBuffer(content));
  assert.equal(createHash('sha256').update(content).digest('hex'), reviewedDigest, '原映射文件已变化');
  // 摘要锁定已审核原字节；完整领域校验仍由目标原resolver负责。
  const mapping = JSON.parse(content.toString('utf8'));
  return {
    mode: 'plan', status: 'awaiting-approval', project, consumer: 'thesis-ledger',
    routeKey: { kind: 'data', market: 'CN', assetType: 'ETF', capability: 'SPLIT_EVENT' },
    target: { providerId: 'akshare', upstreamSource: 'eastmoney' },
    symbol: '159516.SZ', start: '2026-03-30', end: '2026-07-10',
    digest: reviewedDigest, evidenceRef: `sha256:${reviewedDigest}`,
    evidencePath: `/app/data/thesis-ledger-mapping-evidence/${reviewedDigest}.json`,
    revisions: { adapterRevision: 'dsa-eastmoney-fund-split-mapped-v1', sourceRevision: 'eastmoney-fund-cf-pageinfo-json-v1', credentialRevision: 'not-required' },
    validHours: options.validHours, recordedBy: options.recordedBy, validFrom: '应用时UTC',
    mappings: mapping.mappings.map(({ symbol, sourceConversionDate, effectiveDate, sourceRatioPerUnit }) => ({ symbol, sourceConversionDate, effectiveDate, ratio: sourceRatioPerUnit })),
    existingAdmission: '必须不存在；已有记录不续期、不覆盖',
    coverageComplete: false, completeFreeze: '仍受历史覆盖未验证阻断',
    writes: { admission: false, evidence: false, policy: false, credentials: false },
  };
}

export function splitTargetIdentity(container) {
  assert.equal(container.Name, `/${project}-dsa-1`);
  assert.equal(container.Config.Labels['com.docker.compose.project'], project);
  assert.equal(container.Config.Labels['com.docker.compose.service'], 'dsa');
  assert.equal(container.State.Running, true);
  assert.equal(container.State.Health?.Status, 'healthy');
  assert.match(container.Id, /^[a-f0-9]{64}$/);
  assert.match(container.Image, /^sha256:[a-f0-9]{64}$/);
  assert(container.Mounts.some(m => m.Type === 'volume' && m.Name === 'thesis-ledger-dsa-data' && m.Destination === '/app/data' && m.RW === true), '原DSA持久卷不匹配');
  return { id: container.Id, image: container.Image,
    mounts: container.Mounts.map(({ Type, Name, Source, Destination, RW }) => ({ Type, Name, Source, Destination, RW })) };
}

function applyPlan(plan, content) {
  assert(!process.env.DOCKER_HOST && !process.env.BUILDX_BUILDER, '不允许端点覆盖');
  const run = (command, args, input) => execFileSync(command, args, { input, encoding: 'utf8', timeout: 30_000, maxBuffer: 200_000 }).trim();
  const docker = (...args) => run('docker', args);
  assert.equal(docker('context', 'show'), 'desktop-linux');
  const endpoint = docker('context', 'inspect', 'desktop-linux', '--format', '{{.Endpoints.docker.Host}}');
  assert(endpoint.startsWith('unix://'), '仅允许原本地Unix端点');
  const checksum = run('cksum', [], endpoint).split(/\s+/)[0];
  const lock = join(tmpdir(), `thesis-ledger-update-${process.getuid()}-${checksum}.lock`);
  mkdirSync(lock);
  try {
    const name = `${project}-dsa-1`;
    const identity = splitTargetIdentity(JSON.parse(docker('inspect', name))[0]);
    const worker = readFileSync(workerUrl, 'utf8');
    assert.deepEqual(splitTargetIdentity(JSON.parse(docker('inspect', name))[0]), identity, '写入前容器身份变化');
    const payload = { project, symbol: plan.symbol, start: plan.start, end: plan.end,
      digest: plan.digest, validHours: plan.validHours, recordedBy: plan.recordedBy,
      mappingBase64: content.toString('base64') };
    const receipt = JSON.parse(run('docker', ['exec', '-i', '--user', 'dsa:dsa', identity.id, 'python', '-B', '-c', worker], JSON.stringify(payload)));
    assert.deepEqual(splitTargetIdentity(JSON.parse(docker('inspect', name))[0]), identity, '执行后容器身份变化；需读回，不自动重试');
    return receipt;
  } finally { rmdirSync(lock); }
}

export function main(args = process.argv.slice(2), readMapping = () => readFileSync(mappingUrl)) {
  const options = parseSplitAdmissionArguments(args);
  const content = readMapping();
  const plan = splitAdmissionPlan(options, content);
  if (!options.apply) return plan;
  return applyPlan(plan, content);
}
if (import.meta.url === pathToFileURL(process.argv[1]).href) console.log(JSON.stringify(main(), null, 2));
