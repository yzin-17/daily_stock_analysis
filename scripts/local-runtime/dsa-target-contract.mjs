import assert from 'node:assert/strict';

export const project = 'thesis-ledger-dev';
export function assertDsaTarget(info) {
  assert.match(info.Id, /^[a-f0-9]{64}$/);
  assert.match(info.Image, /^sha256:[a-f0-9]{64}$/);
  assert.equal(info.Config.Labels['com.docker.compose.project'], project);
  assert.equal(info.Config.Labels['com.docker.compose.service'], 'dsa');
  assert.equal(info.State.Running, true);
  assert.equal(info.State.Health?.Status, 'healthy');
  assert.equal(info.Config.Labels['org.openai.thesis-ledger.data-runtime'], 'dsa-data-runtime-v1');
}
export function assertOwnedProbe(info, name, image) {
  assert.match(name, /^dsa-dg2a-[a-f0-9-]{36}$/);
  assert.equal(info.Name, '/' + name);
  assert.equal(info.Config.Labels['org.openai.thesis-ledger.dg2a'], name);
  assert.equal(info.Image, image);
  assert.equal(info.HostConfig.NetworkMode, 'none');
  assert.equal(info.HostConfig.ReadonlyRootfs, true);
  assert(info.Mounts.every((mount) => ['bind', 'tmpfs'].includes(mount.Type)), '验收不得接入持久卷');
  const inputs = ['/app/tests', '/app/scripts/data_test_scope.py', '/app/docs/evidence',
    '/app/.env.example', '/thesis-ledger/packages/schemas/fixtures',
    '/app/contracts/thesis-ledger/news-search-v1.samples.json', '/thesis-ledger/packages/schemas/test/fixtures',
    '/app/scripts/export_thesis_ledger_capability_inventory.py', '/app/scripts/check_thesis_ledger_capability_boundaries.py'];
  assert(info.Mounts.filter((mount) => mount.Type === 'bind')
    .every((mount) => mount.RW === false && inputs.includes(mount.Destination)), '验收输入必须只读且限于验证材料');
}
export function assertForwardRequest(path, method) {
  const url = new URL(path, 'http://dg2a.local');
  assert.equal(url.origin, 'http://dg2a.local');
  assert(!url.username && !url.password && !url.hash);
  assert(['GET', 'POST'].includes(method));
  assert(url.pathname.startsWith('/api/v3/thesis-ledger/market/') || url.pathname.startsWith('/api/v3/thesis-ledger/backtest/'));
  return url.pathname + url.search;
}
