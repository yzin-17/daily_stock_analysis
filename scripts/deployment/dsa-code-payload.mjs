import { createHash } from 'node:crypto';
import { cpSync, existsSync, lstatSync, mkdirSync, readFileSync, readdirSync, realpathSync, writeFileSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const VERSION = 'dsa-data-runtime-v1';
const ENTRYPOINT = ['python', '-m', 'uvicorn', 'server:app', '--host', '0.0.0.0', '--port', '8000'];
const FILES = ['server.py', 'scripts/admit_nav_research.py'];
const DIRECTORIES = ['api', 'data_provider', 'src'];
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

export function readDataContract(source) {
  const contract = JSON.parse(readFileSync(join(source, 'scripts/data-runtime-contract.json'), 'utf8'));
  if (contract.contractVersion !== VERSION || !same(contract.entrypoint, ENTRYPOINT)
      || !same(contract.files, FILES) || !same(contract.directories, DIRECTORIES)
      || contract.runtimeBaselinePath !== '/app/scripts/data-runtime-inputs.json') {
    throw new Error('DSA 数据载荷合同不受支持，必须通过完整 update.sh 更新并核对入口');
  }
  return contract;
}

function safePath(root, name) {
  const path = resolve(root, name);
  const inside = relative(resolve(root), path);
  if (!inside || inside.startsWith('..') || inside.startsWith('/') || !existsSync(path)
      || lstatSync(path).isSymbolicLink() || relative(realpathSync(root), realpathSync(path)).startsWith('..')) {
    throw new Error('DSA 载荷路径缺失、越界或使用符号链接');
  }
  return path;
}

export function compatibilityInventory(source) {
  const contract = readDataContract(source);
  const files = {};
  function visit(name) {
    if (name.split('/').some((part) => ['.git', '__pycache__'].includes(part))) return;
    const path = safePath(source, name);
    if (lstatSync(path).isDirectory()) {
      for (const entry of readdirSync(path).sort()) visit(name + '/' + entry);
    } else files[name] = createHash('sha256').update(readFileSync(path)).digest('hex');
  }
  for (const name of contract.compatibilityInputs) visit(name);
  if (!Object.keys(files).length) throw new Error('DSA 兼容性输入不能为空');
  return { schemaVersion: 'dsa-runtime-compatibility-v1', contractVersion: VERSION,
    files: Object.fromEntries(Object.entries(files).sort(([a], [b]) => a.localeCompare(b, 'en'))) };
}

export function verifyCompatibility(source, baseline) {
  const actual = compatibilityInventory(source);
  const observed = JSON.parse(readFileSync(baseline, 'utf8'));
  if (observed.schemaVersion !== actual.schemaVersion || observed.contractVersion !== VERSION
      || !observed.files || Object.keys(observed.files).length !== Object.keys(actual.files).length
      || Object.entries(actual.files).some(([name, hash]) => observed.files[name] !== hash)) {
    throw new Error('DSA 依赖、Dockerfile、原生 SDK 或数据库结构输入变化；快更已拒绝，请使用 ./scripts/update.sh dsa');
  }
}

export function buildDataPayload(source, destination) {
  const contract = readDataContract(source);
  if (existsSync(destination)) throw new Error('DSA 载荷目录已存在，拒绝覆盖');
  mkdirSync(destination, { recursive: true });
  const paths = [...contract.files, ...contract.directories, 'scripts/data-runtime-contract.json'];
  for (const name of paths) {
    const path = safePath(source, name);
    const target = join(destination, name);
    mkdirSync(dirname(target), { recursive: true });
    cpSync(path, target, { recursive: true, filter: (candidate) => {
      if (lstatSync(candidate).isSymbolicLink()) throw new Error('DSA 代码目录不能夹带符号链接');
      return !candidate.split('/').includes('__pycache__') && !/\.(pyc|pyo)$/.test(candidate);
    } });
  }
  writeFileSync(join(destination, 'payload.paths'), paths.join('\n') + '\n');
  writeFileSync(join(destination, 'payload.json'), JSON.stringify({ contractVersion: VERSION, paths }) + '\n');
  return paths;
}

export function verifyCodeMounts(path) {
  const mounts = JSON.parse(readFileSync(path, 'utf8'));
  const codePaths = ['/app/server.py', '/app/api', '/app/src', '/app/data_provider', '/app/scripts'];
  if (!Array.isArray(mounts) || mounts.some((mount) => !mount || typeof mount.Destination !== 'string'
      || codePaths.some((code) => code === mount.Destination || code.startsWith(mount.Destination + '/')
        || mount.Destination.startsWith(code + '/')))) {
    throw new Error('DSA 代码路径存在挂载，不能通过可写层快更；请核对正式部署合同');
  }
}

function main(args) {
  const [command, source, target] = args;
  if (command === 'build' && source && target) buildDataPayload(source, target);
  else if (command === 'verify' && source && target) verifyCompatibility(source, target);
  else if (command === 'contract' && source) readDataContract(source);
  else if (command === 'mounts' && source) verifyCodeMounts(source);
  else throw new Error('用法：dsa-code-payload.mjs <build|verify|contract> <源码> [载荷/基线]');
}
if (process.argv[1] && existsSync(process.argv[1]) && realpathSync(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try { main(process.argv.slice(2)); } catch (error) { process.stderr.write(error.message + '\n'); process.exitCode = 1; }
}
