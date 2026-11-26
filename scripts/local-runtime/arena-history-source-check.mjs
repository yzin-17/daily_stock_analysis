// 历史回放来源的有界 HTTP 验收；不修改来源、路由或比赛。
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { randomUUID } from 'node:crypto';
import { pathToFileURL } from 'node:url';

export function sourceCheckPlan(args) {
  const execute = args.includes('--execute');
  const value = (flag) => { const at = args.indexOf(flag); return at < 0 ? null : args[at + 1]; };
  const date = value('--date'), output = value('--output'), scope = value('--scope') ?? 'all';
  assert(date && /^\d{4}-\d{2}-\d{2}$/.test(date) && new Date(date).toISOString().slice(0, 10) === date, '须指定有效 --date');
  assert(output, '须指定 --output');
  assert(['all', 'catalog', 'minutes'].includes(scope), '来源验收范围无效');
  let maxRequests = 3;
  if (scope === 'catalog') maxRequests = 1;
  else if (scope === 'minutes') maxRequests = 2;
  return { execute, date, scope, output: resolve(output), maxRequests };
}

export async function checkSources(plan, io = {}) {
  if (!plan.execute) return plan;
  const output = plan.output;
  await mkdir(dirname(output), { recursive: true });
  assert(!(await readFile(output).then(() => true, () => false)), '已有回执，不重复执行来源请求');
  await writeFile(output + '.permit', JSON.stringify({ date: plan.date, startedAt: new Date().toISOString() }), { flag: 'wx', mode: 0o600 });
  const inspect = io.inspect ?? (() => JSON.parse(execFileSync('docker', ['inspect', 'thesis-ledger-dev-dsa-1'], { encoding: 'utf8' }))[0]);
  const target = inspect();
  assert(target.State.Running && target.Config.Labels['com.docker.compose.project'] === 'thesis-ledger-dev' && target.Config.Labels['com.docker.compose.service'] === 'dsa', '目标 DSA 身份无效');
  const token = target.Config.Env.find((v) => v.startsWith('THESIS_LEDGER_DSA_TOKEN='))?.slice('THESIS_LEDGER_DSA_TOKEN='.length);
  assert(token, 'DSA 合同凭证缺失');
  const results = [];
  for (const scope of [{ kind: 'catalog' }, { kind: 'minutes', symbol: '600000.SH', assetType: 'STOCK' }, { kind: 'minutes', symbol: '510300.SH', assetType: 'ETF' }]) {
    if (plan.scope !== 'all' && scope.kind !== plan.scope) continue;
    const requestId = randomUUID();
    const { kind, ...identity } = scope;
    try {
      const response = await (io.fetch ?? fetch)(`http://127.0.0.1:8000/api/v1/thesis-ledger/arena/history/${kind}`, {
        method: 'POST', redirect: 'error', signal: AbortSignal.timeout(35000),
        headers: { 'Content-Type': 'application/json', authorization: `Bearer ${token}` },
        body: JSON.stringify({ contractVersion: 1, consumer: 'thesis-ledger', requestId, market: 'CN', date: plan.date, ...identity }),
      });
      const raw = await response.json();
      await writeFile(`${output}.${kind}${identity.assetType ? '-' + identity.assetType : ''}.json`, JSON.stringify(raw), { flag: 'wx', mode: 0o600 });
      const error = raw.detail ?? raw;
      results.push({ ...scope, status: response.status, ready: response.ok, requestId,
        ...(response.ok ? { count: raw.items?.length ?? raw.value?.rows?.length, observedAt: raw.observedAt ?? raw.value?.observedAt,
          providerId: raw.providerId, archiveRef: raw.archiveRef } : { code: error.code, message: error.message }) });
    } catch (error) {
      results.push({ ...scope, ready: false, reason: 'execution_unknown', errorType: error.name });
    }
  }
  const after = inspect();
  assert(after.Id === target.Id && after.State.StartedAt === target.State.StartedAt, '验收期间目标发生变化');
  const receipt = { date: plan.date, target: { id: target.Id, image: target.Image }, results, observedAt: new Date().toISOString() };
  await writeFile(output, JSON.stringify(receipt, null, 2) + '\n', { flag: 'wx', mode: 0o600 });
  return receipt;
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href)
  console.log(JSON.stringify(await checkSources(sourceCheckPlan(process.argv.slice(2)))));
