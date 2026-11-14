import assert from 'node:assert/strict';
import test from 'node:test';
import { assertDsaTarget, assertForwardRequest, assertOwnedProbe } from './dsa-target-contract.mjs';

const name = 'dsa-dg2a-11111111-1111-4111-8111-111111111111';
const image = 'sha256:' + 'a'.repeat(64);
test('目标必须为健康的明确项目数据镜像，旧镜像合同拒绝', () => {
  const info = { Id: 'b'.repeat(64), Image: image, State: { Running: true, Health: { Status: 'healthy' } },
    Config: { Labels: { 'com.docker.compose.project': 'thesis-ledger-dev', 'com.docker.compose.service': 'dsa',
      'org.openai.thesis-ledger.data-runtime': 'dsa-data-runtime-v1' } } };
  assertDsaTarget(info);
  for (const key of Object.keys(info.Config.Labels)) {
    const changed = structuredClone(info); changed.Config.Labels[key] = 'other';
    assert.throws(() => assertDsaTarget(changed));
  }
  const changed = structuredClone(info); changed.State.Health.Status = 'unhealthy';
  assert.throws(() => assertDsaTarget(changed));
});
test('清理只接受本轮精确资源，持久卷及可写输入均拒绝', () => {
  const info = { Name: '/' + name, Image: image, Config: { Labels: { 'org.openai.thesis-ledger.dg2a': name } },
    HostConfig: { NetworkMode: 'none', ReadonlyRootfs: true },
    Mounts: [{ Type: 'tmpfs' }, { Type: 'bind', Destination: '/app/tests', RW: false }] };
  assertOwnedProbe(info, name, image);
  assert.throws(() => assertOwnedProbe({ ...info, Name: '/thesis-ledger-dev-dsa-1' }, name, image));
  assert.throws(() => assertOwnedProbe({ ...info, Mounts: [{ Type: 'volume' }] }, name, image));
  assert.throws(() => assertOwnedProbe({ ...info, Mounts: [{ Type: 'bind', RW: true }] }, name, image));
  assert.throws(() => assertOwnedProbe({ ...info, Mounts: [{ Type: 'bind', RW: false, Destination: '/app/data' }] }, name, image));
  assert.throws(() => assertOwnedProbe({ ...info, HostConfig: { NetworkMode: 'bridge', ReadonlyRootfs: true } }, name, image));
  assert.throws(() => assertOwnedProbe({ ...info, HostConfig: { NetworkMode: 'none', ReadonlyRootfs: false } }, name, image));
});
test('传输桥只接受数据读接口，不向控制面或其他主机转发', () => {
  assertForwardRequest('/api/v3/thesis-ledger/market/bars', 'POST');
  assertForwardRequest('/api/v3/thesis-ledger/backtest/nav-inputs', 'POST');
  for (const path of ['//other.test/x', '/api/v3/thesis-ledger/control/providers', '/api/v3/thesis-ledger/market/../control'])
    assert.throws(() => assertForwardRequest(path, 'POST'));
  assert.throws(() => assertForwardRequest('/api/v3/thesis-ledger/market/bars', 'DELETE'));
});
