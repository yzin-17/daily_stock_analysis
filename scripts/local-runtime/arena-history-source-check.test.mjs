import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { sourceCheckPlan, checkSources } from './arena-history-source-check.mjs';

test('默认预览，执行限制三次、保留来源错误，不输出凭证且拒绝重复许可', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'arena-source-check-'));
  try {
    const args = ['--date', '2026-11-02', '--output', join(directory, 'receipt.json')];
    assert.equal((await checkSources(sourceCheckPlan(args))).execute, false);
    let calls = 0;
    const target = { Id: 'fixture', Image: 'fixture', State: { Running: true, StartedAt: 'fixture' },
      Config: { Labels: { 'com.docker.compose.project': 'thesis-ledger-dev', 'com.docker.compose.service': 'dsa' }, Env: ['THESIS_LEDGER_DSA_TOKEN=synthetic-secret'] } };
    const io = { inspect: () => target, fetch: async () => { calls++; return new Response(JSON.stringify({ code: 'permission_denied', message: '权限不足' }), { status: 503 }); } };
    const result = await checkSources(sourceCheckPlan([...args, '--execute']), io);
    assert.equal(calls, 3);
    assert(result.results.every((r) => r.ready === false && r.code === 'permission_denied'));
    assert(!(await readFile(args[3], 'utf8')).includes('synthetic-secret'));
    await assert.rejects(checkSources(sourceCheckPlan([...args, '--execute']), io));
    assert.equal(calls, 3);
  } finally { await rm(directory, { recursive: true, force: true }); }
});
