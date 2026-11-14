import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

test('应用状态导出保留二进制、目标标签、失败状态与归档排除项', t => {
  const root = mkdtempSync(join(tmpdir(), 'dsa-state-export-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const bin = join(root, 'bin'); mkdirSync(bin);
  const calls = join(root, 'calls');
  writeFileSync(join(bin, 'docker'), `#!/usr/bin/env node
const fs=require('node:fs'),a=process.argv.slice(2);
fs.appendFileSync(process.env.EXPORT_CALLS,JSON.stringify(a)+'\\n');
if(a[0]==='inspect')process.stdout.write(process.env.EXPORT_LABELS);
else {let data='';process.stdin.on('data',v=>data+=v);process.stdin.on('end',()=>{
 if(a.includes('python')&&!data.includes('DATABASE_PATH'))process.exit(91);
 process.stdout.write(Buffer.from([0,255,128,10]));process.exitCode=Number(process.env.EXPORT_STATUS);
});}
`, { mode: 0o755 });
  const entry = fileURLToPath(new URL('./export-state.mjs', import.meta.url));
  const run = (mode, extra = {}) => spawnSync(process.execPath, [entry, mode], { env: { ...process.env,
    PATH: bin + ':' + dirname(process.execPath) + ':' + process.env.PATH,
    EXPORT_CALLS: calls, EXPORT_LABELS: 'thesis-ledger-dev/dsa', EXPORT_STATUS: '0', ...extra } });
  const requests = () => readFileSync(calls, 'utf8').trim().split('\n').map(JSON.parse);
  assert.notEqual(run('unknown').status, 0); assert(!existsSync(calls));
  assert.notEqual(run('snapshot-state', { EXPORT_LABELS: 'production/dsa' }).status, 0);
  assert.equal(requests().length, 1);
  for (const mode of ['snapshot-state', 'archive-state']) {
    const result = run(mode); assert.equal(result.status, 0, result.stderr.toString());
    assert.deepEqual(result.stdout, Buffer.from([0, 255, 128, 10]));
  }
  const archive = requests().find(args => args.includes('tar'));
  for (const name of ['stock_analysis.db', 'stock_analysis.db-wal', 'stock_analysis.db-shm']) assert(archive.includes('--exclude=./' + name));
  assert.equal(run('snapshot-state', { EXPORT_STATUS: '17' }).status, 17);
});
