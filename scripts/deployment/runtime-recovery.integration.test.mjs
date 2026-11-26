import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { DsaRuntimeRecovery } from '../../../thesis-ledger-infra/scripts/dsa-runtime-recovery.mjs';
import { assertRecoveryConfig, recoveryOverride } from './runtime-snapshot.mjs';

function fixture(t) {
  const root = mkdtempSync(join(tmpdir(), 'dsa-recovery-test-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const env = join(root, '.env');
  writeFileSync(env, 'LOCAL_FAKE_KEY=must-never-be-copied\n');
  const oauth = join(root, 'oauth');
  mkdirSync(oauth);
  const runtime = {
    project: 'thesis-ledger-dev', service: 'dsa', imageId: 'sha256:' + 'a'.repeat(64),
    command: ['python', 'main.py', '--serve-only', '--host', '0.0.0.0', '--port', '8000'],
    entrypoint: ['/usr/local/bin/docker-entrypoint.sh'],
    healthcheck: { Test: ['CMD', 'curl', '-f', 'http://localhost:8000/health'],
      Interval: 10_000_000_000, Timeout: 5_000_000_000, Retries: 12 },
    mounts: ['/app/data', '/app/logs', '/app/reports'].map((Destination, index) => ({
      Type: 'volume', Name: ['named-data', 'anonymous-logs', 'anonymous-reports'][index], Destination, RW: true,
    })).concat({ Type: 'bind', Source: oauth, Destination: '/home/dsa/.longbridge', RW: false, Propagation: 'rprivate' }),
    ports: {}, env: ['LOCAL_FAKE_KEY=must-never-be-copied'],
  };
  const state = { context: 'desktop-linux', endpoint: 'unix:///fake-dsa-test.sock',
    runtime, image: runtime.imageId, container: 'dsa-test', health: 'healthy', calls: [], extraMount: false };
  const run = (...input) => {
    const args = input[0] === '--context' ? input.slice(2) : input;
    state.calls.push(args);
    if (args[0] === 'context') return args[1] === 'show' ? state.context : state.endpoint;
    if (args[0] === 'image') return state.image;
    if (args[0] === 'volume') {
      if (state.missingVolume === args[2]) throw new Error('缺少原卷');
      return '[{}]';
    }
    if (args[0] === 'inspect') {
      if (args.includes('{{json .State}}')) return JSON.stringify({ Running: true, Health: { Status: state.health } });
      assert.ok(!args.join(' ').includes('.Config.Env'));
      return JSON.stringify(state.runtime);
    }
    if (args[0] === 'compose') {
      if (args.includes('ps')) return state.container;
      if (args.includes('config')) {
        const override = JSON.parse(readFileSync(args[args.lastIndexOf('-f') + 1], 'utf8'));
        const config = { name: 'thesis-ledger-dev', ...override };
        if (state.extraMount) config.services.dsa.volumes.push({ type: 'bind', source: oauth, target: '/extra' });
        return JSON.stringify(config);
      }
      if (args.includes('up')) return '';
    }
    throw new Error('测试意外调用 Docker: ' + args[0]);
  };
  const controller = new DsaRuntimeRecovery(root, env, run);
  return { root, env, state, controller, run };
}

test('快照只保存恢复字段，具备数据、匿名日志/报告及只读 OAuth 绑定', (t) => {
  const { root, controller, state } = fixture(t);
  const saved = controller.snapshot();
  const bytes = readFileSync(saved.path, 'utf8');
  assert.ok(!bytes.includes('must-never-be-copied'));
  assert.ok(!bytes.includes('env"'));
  assert.equal(statSync(saved.path).mode & 0o777, 0o600);
  const snapshot = JSON.parse(bytes);
  assert.equal(snapshot.mounts.length, 4);
  const override = recoveryOverride(snapshot);
  assert.deepEqual(Object.values(override.volumes).map((v) => v.name).sort(), ['anonymous-logs', 'anonymous-reports', 'named-data']);
  assert.deepEqual(override.services.dsa.command, state.runtime.command);
  assert.equal(override.services.dsa.volumes.find((v) => v.type === 'bind').read_only, true);
  assert.equal(readdirSync(join(root, '.maintenance-backups/dsa-runtime')).filter((name) => /^\d+-/.test(name)).length, 1);
  const retained = JSON.parse(readFileSync(controller.mountFile, 'utf8'));
  assert.equal(retained.services.dsa.volumes.length, 4);
  assert.equal(retained.services.dsa.image, undefined);
  assert.equal(retained.services.dsa.command, undefined);
});

for (const host of ['', '0.0.0.0', '127.0.0.1', '::']) {
  test(`恢复保留默认或显式监听地址：${host || 'Docker 默认地址'}`, (t) => {
    const { controller, state } = fixture(t);
    state.runtime.ports = { '8000/tcp': [{ HostIp: host, HostPort: '8000' }] };
    const saved = controller.snapshot();
    const { snapshot, args } = controller.prepareRestore(saved.path);
    const override = recoveryOverride(snapshot);
    const port = override.services.dsa.ports[0];
    assert.equal(Object.hasOwn(port, 'host_ip'), Boolean(host));
    if (host) assert.equal(port.host_ip, host);
    assert.ok(args.includes('compose'));
    const mismatched = { name: 'thesis-ledger-dev', ...override };
    mismatched.services.dsa.ports[0].host_ip = host ? '' : '127.0.0.1';
    assert.throws(() => assertRecoveryConfig(mismatched, snapshot));
  });
}

for (const mutation of ['environment', 'context', 'image', 'volume', 'bind', 'extra-mount']) {
  test(`恢复前拒绝 ${mutation} 漂移，不启动容器`, async (t) => {
    const { controller, state, env } = fixture(t);
    const saved = controller.snapshot();
    if (mutation === 'environment') writeFileSync(env, 'changed=1');
    if (mutation === 'context') controller.context = 'other';
    if (mutation === 'image') state.image = 'sha256:' + 'b'.repeat(64);
    if (mutation === 'volume') state.missingVolume = 'anonymous-logs';
    if (mutation === 'bind') rmSync(state.runtime.mounts.at(-1).Source, { recursive: true });
    if (mutation === 'extra-mount') state.extraMount = true;
    await assert.rejects(controller.restore(saved.path, 1));
    assert.ok(!state.calls.some((args) => args.includes('up')));
    assert.ok(!state.calls.some((args) => args.includes('pull') || args.includes('build') || args.includes('rm')));
  });
}

test('正式恢复只重建 DSA，固定本地镜像且核对全部运行合同', async (t) => {
  const { controller, state } = fixture(t);
  const saved = controller.snapshot();
  const restored = await controller.restore(saved.path, 1);
  assert.equal(restored.imageId, saved.imageId);
  const up = state.calls.find((args) => args.includes('up'));
  assert.deepEqual(up.slice(up.indexOf('up')), ['up', '-d', '--no-deps', '--no-build', '--pull', 'never', '--force-recreate', 'dsa']);
  assert.ok(state.calls.filter((args) => args[0] === 'volume').every((args) => args[1] === 'inspect'));
});

test('无旧容器时快照为空；查询失败不得当成没有容器', (t) => {
  const { controller, state } = fixture(t);
  state.container = '';
  assert.equal(controller.snapshot(), null);
  controller.docker = () => { throw new Error('查询失败'); };
  assert.throws(() => controller.snapshot(), /查询失败/);
});

test('健康失败保留现场，不自动删除或替换其他服务', async (t) => {
  const { controller, state } = fixture(t);
  const saved = controller.snapshot();
  state.health = 'unhealthy';
  await assert.rejects(controller.restore(saved.path, 1), /不健康|未健康/);
  assert.equal(state.calls.filter((args) => args.includes('up')).length, 1);
  assert.ok(!state.calls.some((args) => args.includes('down') || args.includes('rm') || args.includes('prune')));
});

test('恢复后镜像或挂载不一致必须失败', async (t) => {
  const { controller, state } = fixture(t);
  const saved = controller.snapshot();
  state.runtime.imageId = 'sha256:' + 'b'.repeat(64);
  await assert.rejects(controller.restore(saved.path, 1), /合同不匹配/);
});
