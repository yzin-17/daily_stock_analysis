import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { ownsUpdateLock } from '../../../thesis-ledger-infra/scripts/runtime/lock.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
assert(ownsUpdateLock(process.env.INFRA_UPDATE_LOCK_PATH ?? ''), '需要正式维护锁');
const [source, backup, confirm] = process.argv.slice(2);
assert.equal(process.argv.length, 5);
for (const path of [source, backup])
  assert(resolve(path).startsWith(resolve(root, '../thesis-ledger/.recovery-artifacts') + '/'));
console.log(execFileSync(resolve(root, '.venv/bin/python'), ['-B', 'scripts/local-runtime/historical-execution-range.py'], {
  cwd: root, env: { ...process.env, PYTHONPATH: root, PYTHONDONTWRITEBYTECODE: '1' },
  input: JSON.stringify({ mode: 'promote', source, backup, confirm }), encoding: 'utf8', timeout: 60_000,
}).trim());
