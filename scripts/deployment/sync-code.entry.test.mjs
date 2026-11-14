import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { cpSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { compatibilityInventory } from './dsa-code-payload.mjs';

const application = fileURLToPath(new URL('../../', import.meta.url));
const scripts = fileURLToPath(new URL('../../../thesis-ledger-infra/scripts/', import.meta.url));
const fakeDocker = `#!/usr/bin/env node
const fs = require('node:fs');
const path = require('node:path');
const raw = process.argv.slice(2);
const a = raw[0] === '--context' ? raw.slice(2) : raw;
const state = JSON.parse(fs.readFileSync(process.env.DSA_TEST_STATE));
fs.appendFileSync(process.env.DSA_TEST_LOG, JSON.stringify(a) + '\\n');
const out = (v) => process.stdout.write(typeof v === 'string' ? v : JSON.stringify(v));
if(a[0] === 'info') out('ok');
else if(a[0] === 'context') out(a[1] === 'show' ? 'desktop-linux' : 'unix://' + process.env.DSA_TEST_STATE);
else if(a[0] === 'image') out(state.referenceImage ?? state.image);
else if(a[0] === 'inspect') {
 const f=a[a.indexOf('--format')+1];
 if(f === '{{.State.Status}}') out(state.exited ? 'exited' : 'running');
 else if(f === '{{.Image}}') out(state.image);
 else if(f === '{{.Config.Image}}') out('daily-stock-analysis:thesisledger-dev');
 else if(f === '{{json .Config.Cmd}}') out(state.command);
 else if(f === '{{json .Mounts}}') out(state.mounts);
 else if(f.includes('.State.Health')) out(state.noHealth ? 'none' : 'healthy');
 else process.exit(91);
} else if(a[0] === 'cp') {
 if(a[1].startsWith('owned-dsa:/app/')) {
  const name=a[1].slice('owned-dsa:/app/'.length);
  if(name === 'scripts/data-runtime-inputs.json') {
   if(state.noBaseline) process.exit(1);
   fs.copyFileSync(state.baseline, a[2]);
  } else fs.copyFileSync(path.join(state.source,name), a[2]);
 }
} else if(a[0] === 'exec') {
 if(a.includes('sh') && state.applyFailure) process.exit(17);
} else if(a[0] === 'compose') {
 if(a.includes('ps')) out('owned-dsa');
 else if(a.includes('restart')) {
  state.exited=state.healthFailure;
  fs.writeFileSync(process.env.DSA_TEST_STATE, JSON.stringify(state));
 } else if(a.includes('up')) {
  state.exited=false;
  fs.writeFileSync(process.env.DSA_TEST_STATE, JSON.stringify(state));
 } else process.exit(92);
} else process.exit(93);
`;

function fixture(t, options = {}) {
  const root = mkdtempSync(join(tmpdir(), 'dsa-sync-entry-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const infra = join(root, 'infra'), source = join(root, 'source');
  mkdirSync(join(infra, 'scripts'), { recursive: true }); mkdirSync(join(root, 'bin'));
  cpSync(scripts, join(infra, 'scripts'), { recursive: true, filter: path => !path.includes('.test.') && !path.includes('/fixtures') });
  const contract = JSON.parse(readFileSync(join(application, 'scripts/deployment/fixtures/data-runtime-contract.json'), 'utf8'));
  mkdirSync(join(source, 'scripts'), { recursive: true });
  writeFileSync(join(source, 'scripts/data-runtime-contract.json'), JSON.stringify(contract));
  for (const name of new Set([...contract.files, ...contract.compatibilityInputs])) {
    const path = join(source, name); mkdirSync(dirname(path), { recursive: true });
    if (name === 'patches/longbridge-sdk') { mkdirSync(path); writeFileSync(join(path, 'sdk.patch'), 'native'); }
    else writeFileSync(path, 'controlled-input');
  }
  for (const name of contract.directories) mkdirSync(join(source, name), { recursive: true });
  for (const name of ['scripts/local-deployment.sh', 'scripts/deployment/preflight.sh', 'scripts/deployment/dsa-code-payload.mjs', 'scripts/deployment/apply-code.sh']) {
    mkdirSync(dirname(join(source, name)), { recursive: true });
    cpSync(join(application, name), join(source, name));
  }
  const baseline = join(root, 'baseline.json');
  writeFileSync(baseline, JSON.stringify(compatibilityInventory(source)));
  if (options.drift) writeFileSync(join(source, options.drift), 'changed ABI');
  const state = { source, baseline, image: 'sha256:' + 'a'.repeat(64), command: contract.entrypoint,
    mounts: [{ Destination: '/app/data' }, { Destination: '/app/logs' }], ...options };
  const stateFile = join(root, 'state.json'), log = join(root, 'calls.jsonl');
  writeFileSync(stateFile, JSON.stringify(state)); writeFileSync(log, '');
  writeFileSync(join(infra, '.env'), 'fake=1');
  for (const name of ['compose.yml', 'compose.dev.yml']) writeFileSync(join(infra, name), 'services: {}\n');
  writeFileSync(join(root, 'bin/docker'), fakeDocker, { mode: 0o755 });
  for (const name of ['npm', 'pnpm']) writeFileSync(join(root, 'bin', name), '#!/bin/sh\nexit 99\n', { mode: 0o755 });
  const env = { PATH: join(root, 'bin') + ':' + process.env.PATH, HOME: root, TMPDIR: root,
    DSA_SOURCE_DIR: source, DSA_TEST_STATE: stateFile, DSA_TEST_LOG: log, HEALTH_TIMEOUT_SECONDS: '1' };
  const result = spawnSync('bash', ['scripts/sync-code.sh', 'dsa'], { cwd: infra, env, encoding: 'utf8' });
  const calls = readFileSync(log, 'utf8').trim().split('\n').filter(Boolean).map(JSON.parse);
  return { root, result, calls };
}

test('真实快更入口只装配数据，不运行 npm，重启后核对同一镜像', (t) => {
  const { result, calls } = fixture(t);
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert.match(result.stdout, /镜像 ID 保持不变/);
  assert.equal(calls.filter((a) => a.includes('restart')).length, 1);
  assert.ok(!calls.some((a) => ['up', 'build', 'pull', 'run', 'down'].some((v) => a.includes(v))));
  const copy = calls.find((a) => a[0] === 'cp' && !a[1].startsWith('owned-dsa:'));
  assert.ok(copy[1].includes('dsa-payload/'));
});

for (const [name, options] of [
  ['旧镜像无基线', { noBaseline: true }],
  ['旧产品启动命令', { command: ['python', 'main.py', '--serve-only'] }],
  ['依赖漂移', { drift: 'requirements.txt' }],
  ['原生 SDK 漂移', { drift: 'patches/longbridge-sdk/sdk.patch' }],
  ['存储结构输入漂移', { drift: 'src/services/thesis_ledger_control.py' }],
  ['代码挂载', { mounts: [{ Destination: '/app/src' }] }],
  ['缺少健康检查', { noHealth: true }],
  ['镜像标签漂移', { referenceImage: 'sha256:' + 'b'.repeat(64) }],
]) {
  test(`真实快更入口在任何容器变更前拒绝：${name}`, (t) => {
    const { result, calls } = fixture(t, options);
    assert.equal(result.status, 1, result.stdout + result.stderr);
    assert.ok(!calls.some((a) => ['exec', 'restart', 'up'].some((word) => a.includes(word))));
    assert.ok(!calls.some((a) => a[0] === 'cp' && !a[1].startsWith('owned-dsa:')));
  });
}

for (const options of [{ applyFailure: true }, { healthFailure: true }]) {
  test(`真实快更失败仅用原镜像重建 DSA：${Object.keys(options)[0]}`, (t) => {
    const { result, calls } = fixture(t, options);
    assert.notEqual(result.status, 0);
    const up = calls.find((a) => a.includes('up'));
    assert.ok(up, result.stdout + result.stderr);
    assert.deepEqual(up.slice(up.indexOf('up')), ['up', '-d', '--no-build', '--no-deps', '--pull', 'never', '--force-recreate', 'dsa']);
    assert.ok(!calls.some((a) => a.includes('down') || a.includes('volume') || a.includes('prune')));
  });
}
