import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { ownsUpdateLock } from '../../../thesis-ledger-infra/scripts/runtime/lock.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
assert(ownsUpdateLock(process.env.INFRA_UPDATE_LOCK_PATH ?? ''), '需要正式维护锁');
const [source, confirm] = process.argv.slice(2);
assert.equal(process.argv.length, 4);
assert(resolve(source).startsWith(resolve(root, '../thesis-ledger/.recovery-artifacts') + '/'));
console.log(execFileSync(resolve(root, '.venv/bin/python'), ['-B', 'scripts/local-runtime/sse-event-coverage-admission.py'], {
  cwd: root, env: { ...process.env, PYTHONPATH: root, PYTHONDONTWRITEBYTECODE: '1' },
  input: JSON.stringify({ mode: 'promote', source, confirm }), encoding: 'utf8', timeout: 60_000,
}).trim());
