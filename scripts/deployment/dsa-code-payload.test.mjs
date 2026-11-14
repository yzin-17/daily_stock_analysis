import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';
import { buildDataPayload, compatibilityInventory, verifyCodeMounts, verifyCompatibility } from './dsa-code-payload.mjs';

const infra = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const actual = infra;

function fixture(t) {
  const root = mkdtempSync(join(tmpdir(), 'dsa-code-contract-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const source = join(root, 'source');
  mkdirSync(join(source, 'scripts'), { recursive: true });
  const contract = JSON.parse(readFileSync(join(infra, 'scripts/deployment/fixtures/data-runtime-contract.json'), 'utf8'));
  writeFileSync(join(source, 'scripts/data-runtime-contract.json'), JSON.stringify(contract));
  for (const name of new Set([...contract.files, ...contract.compatibilityInputs])) {
    const path = join(source, name);
    mkdirSync(dirname(path), { recursive: true });
    if (name === 'patches/longbridge-sdk') {
      mkdirSync(path); writeFileSync(join(path, 'native.patch'), '受控原生输入');
    } else writeFileSync(path, '受控输入');
  }
  for (const name of contract.directories) mkdirSync(join(source, name), { recursive: true });
  return { root, source, contract };
}

test('实际 Python 镜像基线与 Node 宿主预检对同一输入完全一致', {
  skip: !existsSync(join(actual, 'scripts/data_runtime_compatibility.py')) && '需要同级 DSA 源码，独立 infra CI 只验证固定合同 fixture',
}, (t) => {
  const { root } = fixture(t);
  const output = join(root, 'baseline.json');
  const python = process.env.DSA_PYTHON ?? 'python3';
  assert.deepEqual(JSON.parse(readFileSync(join(actual, 'scripts/data-runtime-contract.json'))),
    JSON.parse(readFileSync(join(infra, 'scripts/deployment/fixtures/data-runtime-contract.json'))));
  execFileSync(python, [join(actual, 'scripts/data_runtime_compatibility.py'), '--root', actual, '--output', output]);
  assert.deepEqual(compatibilityInventory(actual), JSON.parse(readFileSync(output, 'utf8')));
  verifyCompatibility(actual, output);
});

test('载荷只复制明确代码及资产，保留受控数据字节，不构建 Web', (t) => {
  const { root, source } = fixture(t);
  mkdirSync(join(source, 'src/data/assets'), { recursive: true });
  writeFileSync(join(source, 'src/data/assets/stocks.index.json'), '受控索引原字节');
  for (const name of ['data', 'reports', 'bot', 'strategies', 'static']) {
    mkdirSync(join(source, name)); writeFileSync(join(source, name, 'private'), '不得复制');
  }
  mkdirSync(join(source, 'src/__pycache__'));
  writeFileSync(join(source, 'src/__pycache__/cached.pyc'), '缓存');
  const target = join(root, 'payload');
  const paths = buildDataPayload(source, target);
  assert.deepEqual(new Set(paths), new Set(['server.py', 'scripts/admit_nav_research.py', 'api',
    'data_provider', 'src', 'scripts/data-runtime-contract.json']));
  assert.equal(readFileSync(join(target, 'src/data/assets/stocks.index.json'), 'utf8'), '受控索引原字节');
  assert.ok(!existsSync(join(target, 'src/__pycache__')));
  for (const name of ['data', 'reports', 'bot', 'strategies', 'static']) assert.ok(!existsSync(join(target, name)));
});

for (const name of ['.dockerignore', 'requirements.txt', 'docker/Dockerfile', 'patches/longbridge-sdk/native.patch',
  'src/services/thesis_ledger_control.py']) {
  test(`兼容性预检拒绝输入漂移：${name}`, (t) => {
    const { root, source } = fixture(t);
    const baseline = join(root, 'baseline.json');
    writeFileSync(baseline, JSON.stringify(compatibilityInventory(source)));
    writeFileSync(join(source, name), '已变化');
    assert.throws(() => verifyCompatibility(source, baseline), /快更已拒绝/);
  });
}

test('普通 Adapter 变化可以快更，旧 profile 和外部链接被拒绝', (t) => {
  const { root, source, contract } = fixture(t);
  const baseline = join(root, 'baseline.json');
  writeFileSync(baseline, JSON.stringify(compatibilityInventory(source)));
  writeFileSync(join(source, 'src/adapter.py'), '普通代码');
  verifyCompatibility(source, baseline);
  symlinkSync(join(root, 'external'), join(source, 'src/link'));
  assert.throws(() => buildDataPayload(source, join(root, 'payload')), /符号链接/);
  contract.contractVersion = 'legacy';
  writeFileSync(join(source, 'scripts/data-runtime-contract.json'), JSON.stringify(contract));
  assert.throws(() => compatibilityInventory(source), /不受支持/);
});

test('只允许代码在可写层，业务数据挂载不受影响', (t) => {
  const { root } = fixture(t);
  const file = join(root, 'mounts.json');
  writeFileSync(file, JSON.stringify([{ Destination: '/app/data' }, { Destination: '/app/reports' }]));
  verifyCodeMounts(file);
  for (const Destination of ['/app', '/app/src', '/app/api/custom']) {
    writeFileSync(file, JSON.stringify([{ Destination }]));
    assert.throws(() => verifyCodeMounts(file), /代码路径存在挂载/);
  }
});
