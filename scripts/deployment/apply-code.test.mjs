import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

const scripts = dirname(fileURLToPath(import.meta.url));
const paths = ['server.py', 'scripts/admit_nav_research.py', 'api', 'data_provider', 'src', 'scripts/data-runtime-contract.json'];

function digestTree(root) {
  const result = {};
  const visit = (folder) => {
    for (const name of readdirSync(join(root, folder)).sort()) {
      const relative = folder ? `${folder}/${name}` : name;
      if (statSync(join(root, relative)).isDirectory()) visit(relative);
      else result[relative] = createHash('sha256').update(readFileSync(join(root, relative))).digest('hex');
    }
  };
  visit(''); return result;
}

function fixture(t) {
  const root = mkdtempSync(join(tmpdir(), 'dsa-file-transaction-'));
  const stage = mkdtempSync('/tmp/thesis-ledger-code-sync.');
  t.after(() => { rmSync(root, { recursive: true, force: true }); rmSync(stage, { recursive: true, force: true }); });
  const app = join(root, 'app'); const bin = join(root, 'bin'); mkdirSync(app); mkdirSync(bin);
  for (const base of [app, stage]) {
    for (const name of paths) {
      const target = join(base, name); mkdirSync(dirname(target), { recursive: true });
      if (!name.includes('.')) { mkdirSync(target); writeFileSync(join(target, 'module.py'), base === app ? 'old' : 'new'); }
      else writeFileSync(target, base === app ? 'old' : 'new');
    }
  }
  for (const name of ['data', 'logs', 'reports', 'oauth']) {
    mkdirSync(join(app, name)); writeFileSync(join(app, name, 'retained'), 'original-persistent-bytes');
  }
  writeFileSync(join(stage, 'payload.paths'), paths.join('\n') + '\n');
  writeFileSync(join(bin, 'chown'), `#!/bin/sh
for target do :; done
if [ "$target" = "\${DSA_TEST_FAIL_TARGET:-}" ]; then exit 9; fi
`, { mode: 0o755 });
  // 只将固定 /app 根映射到本用例目录；事务控制流、路径白名单、备份及回滚均执行原脚本。
  const actual = readFileSync(join(scripts, 'apply-code.sh'), 'utf8').replaceAll('/app/', app + '/');
  const run = (extra = {}) => spawnSync('sh', ['-s', stage], { input: actual, encoding: 'utf8',
    env: { PATH: bin + ':' + process.env.PATH, ...extra } });
  return { root, app, stage, run };
}

test('实际文件事务替换所有代码，保留全部持久化字节', (t) => {
  const { app, run } = fixture(t);
  const result = run(); assert.equal(result.status, 0, result.stderr);
  assert.equal(readFileSync(join(app, 'api/module.py'), 'utf8'), 'new');
  assert.equal(readFileSync(join(app, 'scripts/admit_nav_research.py'), 'utf8'), 'new');
  for (const name of ['data', 'logs', 'reports', 'oauth']) {
    assert.equal(readFileSync(join(app, name, 'retained'), 'utf8'), 'original-persistent-bytes');
  }
});

test('中途权限步骤失败，已替换代码全部回滚到原字节', (t) => {
  const { app, run } = fixture(t); const before = digestTree(app);
  const result = run({ DSA_TEST_FAIL_TARGET: join(app, 'src') });
  assert.notEqual(result.status, 0);
  assert.deepEqual(digestTree(app), before);
});

for (const manifest of ['', 'server.py\n', paths.concat('data').join('\n') + '\n', paths.concat('server.py').join('\n') + '\n']) {
  test(`不完整、越权或重复清单在替换前拒绝：${manifest.length} 字节`, (t) => {
    const { app, stage, run } = fixture(t); const before = digestTree(app);
    writeFileSync(join(stage, 'payload.paths'), manifest);
    assert.notEqual(run().status, 0); assert.deepEqual(digestTree(app), before);
  });
}
