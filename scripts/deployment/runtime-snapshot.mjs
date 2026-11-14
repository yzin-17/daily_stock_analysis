import { createHash } from 'node:crypto';

export const SNAPSHOT_VERSION = 'dsa-runtime-recovery-v1';
const dataCommand = ['python', '-m', 'uvicorn', 'server:app', '--host', '0.0.0.0', '--port', '8000'];
const legacyCommand = ['python', 'main.py', '--serve-only', '--host', '0.0.0.0', '--port', '8000'];
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const fail = (message) => { throw new Error(message); };
export const fingerprint = (bytes) => createHash('sha256').update(bytes).digest('hex');
const healthIdentity = (health) => [health.Test, ...['Interval', 'Timeout', 'StartPeriod', 'StartInterval', 'Retries']
  .map((key) => health[key] ?? 0)];

function validateMounts(mounts) {
  if (!Array.isArray(mounts) || !mounts.length) fail('DSA 恢复挂载信息缺失');
  const targets = new Set();
  for (const mount of mounts) {
    if (!['volume', 'bind'].includes(mount.Type) || typeof mount.RW !== 'boolean'
        || typeof mount.Destination !== 'string' || !mount.Destination.startsWith('/')
        || targets.has(mount.Destination)) fail('DSA 恢复挂载类型、目标或权限无法确认');
    targets.add(mount.Destination);
    if (mount.Type === 'volume' && !/^[a-zA-Z0-9][a-zA-Z0-9_.-]*$/.test(mount.Name ?? '')) {
      fail('DSA 恢复卷必须指向已存在的精确卷名');
    }
    if (mount.Type === 'bind' && (typeof mount.Source !== 'string' || !mount.Source.startsWith('/'))) {
      fail('DSA 恢复绑定目录必须使用绝对路径');
    }
  }
  if (!targets.has('/app/data')) fail('DSA 恢复缺少数据挂载');
}

export function normalizeMounts(mounts) {
  validateMounts(mounts);
  return mounts.map((mount) => ({ type: mount.Type,
    source: mount.Type === 'volume' ? mount.Name : mount.Source,
    target: mount.Destination, readOnly: !mount.RW,
    propagation: mount.Type === 'bind' ? (mount.Propagation || 'rprivate') : null,
  })).sort((a, b) => a.target.localeCompare(b.target));
}

export function normalizePorts(ports = {}) {
  if (!ports || typeof ports !== 'object' || Array.isArray(ports)) fail('DSA 端口绑定缺失');
  const result = [];
  for (const [target, bindings] of Object.entries(ports)) {
    if (!/^\d+\/(tcp|udp)$/.test(target) || !Array.isArray(bindings)) fail('DSA 端口绑定无效');
    for (const binding of bindings) {
      if (typeof binding.HostIp !== 'string' || !/^\d+$/.test(binding.HostPort ?? '')) fail('DSA 端口绑定无效');
      result.push({ target, host: binding.HostIp, port: binding.HostPort });
    }
  }
  return result.sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b)));
}

export function validateSnapshot(snapshot) {
  if (snapshot.schemaVersion !== SNAPSHOT_VERSION || snapshot.project !== 'thesis-ledger-dev'
      || !/^sha256:[a-f0-9]{64}$/.test(snapshot.imageId ?? '')
      || !/^[a-f0-9]{64}$/.test(snapshot.envSha256 ?? '')
      || typeof snapshot.dockerContext !== 'string' || !snapshot.dockerContext
      || !snapshot.dockerEndpoint?.startsWith('unix://')) fail('DSA 恢复快照身份不完整');
  if (!same(snapshot.command, dataCommand) && !same(snapshot.command, legacyCommand)) {
    fail('DSA 恢复仅支持数据 API 或原 serve-only API 命令');
  }
  if (snapshot.entrypoint !== null && !same(snapshot.entrypoint, [])
      && !same(snapshot.entrypoint, ['/usr/local/bin/docker-entrypoint.sh'])) fail('DSA 恢复入口不受支持');
  const health = snapshot.healthcheck;
  const acceptedHealth = [['CMD', 'curl', '-f', 'http://localhost:8000/health'],
    ['CMD-SHELL', 'curl -f http://localhost:8000/health || exit 1']];
  if (!health || !acceptedHealth.some((test) => same(test, health.Test))) fail('DSA 健康检查无法安全恢复');
  for (const key of ['Interval', 'Timeout', 'StartPeriod', 'StartInterval', 'Retries']) {
    if (health[key] !== undefined && (!Number.isSafeInteger(health[key]) || health[key] < 0)) fail('DSA 健康检查参数无效');
  }
  normalizeMounts(snapshot.mounts);
  normalizePorts(snapshot.ports);
  return snapshot;
}

export function snapshotFromInspection(runtime, identity) {
  if (runtime.project !== 'thesis-ledger-dev' || runtime.service !== 'dsa') fail('容器不属于本项目 DSA');
  const mounts = runtime.mounts?.map((mount) => ({ Type: mount.Type, Name: mount.Name,
    Source: mount.Type === 'bind' ? mount.Source : undefined, Destination: mount.Destination,
    RW: mount.RW, Propagation: mount.Type === 'bind' ? mount.Propagation : undefined }));
  return validateSnapshot({ schemaVersion: SNAPSHOT_VERSION, createdAt: new Date().toISOString(),
    ...identity, project: runtime.project, imageId: runtime.imageId, command: runtime.command,
    entrypoint: runtime.entrypoint, healthcheck: runtime.healthcheck, mounts, ports: runtime.ports ?? {} });
}

export function recoveryOverride(snapshot) {
  validateSnapshot(snapshot);
  const volumes = {};
  const mounts = normalizeMounts(snapshot.mounts).map((mount, index) => {
    if (mount.type === 'volume') {
      const key = `dsa_restore_${index}`;
      volumes[key] = { external: true, name: mount.source };
      return { type: 'volume', source: key, target: mount.target, read_only: mount.readOnly,
        volume: { nocopy: true } };
    }
    return { type: 'bind', source: mount.source, target: mount.target, read_only: mount.readOnly,
      bind: { create_host_path: false, propagation: mount.propagation } };
  });
  const healthcheck = { test: snapshot.healthcheck.Test };
  for (const [source, key] of [['Interval', 'interval'], ['Timeout', 'timeout'],
    ['StartPeriod', 'start_period'], ['StartInterval', 'start_interval']]) {
    if (snapshot.healthcheck[source] !== undefined) healthcheck[key] = `${snapshot.healthcheck[source]}ns`;
  }
  if (snapshot.healthcheck.Retries !== undefined) healthcheck.retries = snapshot.healthcheck.Retries;
  const ports = normalizePorts(snapshot.ports).map((port) => ({
    target: Number(port.target.split('/')[0]), protocol: port.target.split('/')[1],
    published: port.port, ...(port.host ? { host_ip: port.host } : {}),
  }));
  return { services: { dsa: { image: snapshot.imageId, command: snapshot.command,
    entrypoint: snapshot.entrypoint ?? [], healthcheck, volumes: mounts, ports } }, volumes };
}

/** Compose 合并后必须仍为原挂载及端口，避免当前配置悄悄新增绑定。 */
export function assertRecoveryConfig(config, snapshot) {
  validateSnapshot(snapshot);
  const service = config.services?.dsa;
  if (config.name !== snapshot.project || !service || service.image !== snapshot.imageId
      || !same(service.command, snapshot.command) || !same(service.entrypoint ?? [], snapshot.entrypoint ?? [])) {
    fail('恢复配置未固定原镜像和 API 命令');
  }
  const mounts = (service.volumes ?? []).map((mount) => ({
    Type: mount.type, Name: config.volumes?.[mount.source]?.name,
    Source: mount.source, Destination: mount.target, RW: !(mount.read_only ?? false),
    Propagation: mount.bind?.propagation,
  }));
  if (!same(normalizeMounts(mounts), normalizeMounts(snapshot.mounts))) fail('恢复配置改变了原挂载');
  const ports = {};
  for (const port of service.ports ?? []) {
    const target = `${port.target}/${port.protocol ?? 'tcp'}`;
    (ports[target] ??= []).push({ HostIp: port.host_ip ?? '', HostPort: String(port.published) });
  }
  if (!same(normalizePorts(ports), normalizePorts(snapshot.ports))) fail('恢复配置改变了原端口');
}

export function assertRestoredRuntime(runtime, snapshot) {
  const actual = snapshotFromInspection(runtime, { dockerContext: snapshot.dockerContext,
    dockerEndpoint: snapshot.dockerEndpoint, envSha256: snapshot.envSha256 });
  for (const key of ['imageId', 'command']) {
    if (!same(actual[key], snapshot[key])) fail('恢复后的运行合同不匹配');
  }
  if (!same(actual.entrypoint ?? [], snapshot.entrypoint ?? [])
      || !same(healthIdentity(actual.healthcheck), healthIdentity(snapshot.healthcheck))) fail('恢复后的入口或健康合同不匹配');
  if (!same(normalizeMounts(actual.mounts), normalizeMounts(snapshot.mounts))
      || !same(normalizePorts(actual.ports), normalizePorts(snapshot.ports))) fail('恢复后的挂载或端口不匹配');
}
