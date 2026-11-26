import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { ownsUpdateLock } from '../../../thesis-ledger-infra/scripts/runtime/lock.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
assert(ownsUpdateLock(process.env.INFRA_UPDATE_LOCK_PATH ?? ''), '必须通过 infra 正式持锁入口加载材料');
const [source, confirm] = process.argv.slice(2);
assert.equal(process.argv.length, 4, '需要材料目录与已核验索引摘要');
assert(source?.startsWith(resolve(root, '../thesis-ledger/.recovery-artifacts') + '/'), '只加载私有核验材料');
const result = execFileSync(resolve(root, '.venv/bin/python'), ['-B', 'scripts/local-runtime/historical-execution-admission.py'], {
  cwd: root, env: { ...process.env, PYTHONPATH: root, PYTHONDONTWRITEBYTECODE: '1' },
  input: JSON.stringify({ mode: 'promote', source, confirm }), encoding: 'utf8', timeout: 30_000,
});
console.log(result.trim());
