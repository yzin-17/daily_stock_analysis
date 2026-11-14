import { execFileSync, spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { appendFileSync, cpSync, existsSync, lstatSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, renameSync, rmSync } from 'node:fs';
import { isAbsolute, join } from 'node:path';
import { fileURLToPath } from 'node:url';

export function sourceMetadata() {
  const data = JSON.parse(readFileSync(new URL('./sdk-source.json', import.meta.url), 'utf8'));
  const url = new URL(data.upstream);
  if (url.protocol !== 'https:' || url.username || url.password || !/^[a-f0-9]{40}$/.test(data.revision) || !/^v\d+\.\d+\.\d+$/.test(data.tag)) throw new Error('SDK 固定源码元数据无效。');
  return data;
}

function sourceEnvironment(env, platform) {
  const result = { ...env };
  if (platform !== 'darwin' || env.HTTPS_PROXY || env.https_proxy || env.ALL_PROXY || env.all_proxy) return result;
  const text = execFileSync('/usr/sbin/scutil', ['--proxy'], { encoding: 'utf8' });
  const values = Object.fromEntries([...text.matchAll(/^\s*(HTTPSEnable|HTTPSProxy|HTTPSPort)\s*:\s*(\S+)/gm)].map(match => [match[1], match[2]]));
  if (values.HTTPSEnable === '1' && ['localhost', '127.0.0.1'].includes(values.HTTPSProxy)
      && /^\d+$/.test(values.HTTPSPort) && Number(values.HTTPSPort) > 0 && Number(values.HTTPSPort) < 65536) result.HTTPS_PROXY = `http://${values.HTTPSProxy}:${values.HTTPSPort}`;
  return result;
}

export function prepareLocalSource(directory, cacheRoot, { env = process.env, platform = process.platform, metadata = sourceMetadata(), run, log = console.log } = {}) {
  if (![directory, cacheRoot].every(value => typeof value === 'string' && isAbsolute(value))) throw new Error('构建工作目录和缓存目录必须为绝对路径。');
  const work = join(directory, 'sdk-source'), output = join(directory, 'sdk-source-output'), source = join(work, 'source');
  if (existsSync(source)) throw new Error('工作目录已有 source，拒绝覆盖。');
  mkdirSync(work, { recursive: true }); mkdirSync(output, { recursive: true });
  const sourceEnv = sourceEnvironment(env, platform);
  const buildLog = join(work, 'build.log');
  const git = args => {
    if (run) return run(args, { cwd: source, env: sourceEnv }).trim();
    const result = spawnSync('git', args, { cwd: args[0] === 'clone' ? work : source, env: sourceEnv, encoding: 'utf8', maxBuffer: 16 * 1024 * 1024 });
    appendFileSync(buildLog, `${result.stdout ?? ''}${result.stderr ?? ''}${result.error?.message ?? ''}`);
    if (result.status !== 0) throw new Error(`SDK 固定源码检查失败，详情见 ${buildLog}`);
    return result.stdout.trim();
  };
  const key = createHash('sha256').update(readFileSync(new URL('./sdk-source.json', import.meta.url)))
    .update(readFileSync(fileURLToPath(import.meta.url))).digest('hex');
  const cached = join(cacheRoot, key), cachedSource = join(cached, 'source');
  if (existsSync(cachedSource)) {
    if (!lstatSync(cachedSource).isDirectory() || lstatSync(cachedSource).isSymbolicLink()) throw new Error('SDK 缓存源必须为普通目录。');
    cpSync(cachedSource, source, { recursive: true, dereference: false, preserveTimestamps: true });
    log('复用固定 SDK 源码缓存，继续执行 revision、Git 对象及工作树检查。');
  } else git(['clone', '--depth', '1', '--branch', metadata.tag, '--', metadata.upstream, source]);
  if (!existsSync(join(source, '.git')) || !lstatSync(join(source, '.git')).isDirectory() || lstatSync(join(source, '.git')).isSymbolicLink()) throw new Error('SDK 源必须包含自身 Git 对象目录。');
  if (git(['rev-parse', 'HEAD']) !== metadata.revision) throw new Error('SDK 上游 tag 与固定 revision 不符，停止构建。');
  git(['fsck', '--strict', '--no-reflogs']); git(['reset', '--mixed', 'HEAD']); git(['diff', '--exit-code', 'HEAD']);
  if (git(['ls-files', '--others'])) throw new Error('SDK 固定源码含未跟踪文件，停止构建。');
  for (const name of ['logs', 'hooks']) rmSync(join(source, '.git', name), { recursive: true, force: true });
  rmSync(join(source, '.git/index'), { force: true });
  if (!existsSync(cachedSource)) {
    mkdirSync(cacheRoot, { recursive: true });
    const temporary = mkdtempSync(join(cacheRoot, '.source.'));
    try { cpSync(source, join(temporary, 'source'), { recursive: true, dereference: false, preserveTimestamps: true }); renameSync(temporary, cached); }
    finally { rmSync(temporary, { recursive: true, force: true }); }
  }
  log(`固定 SDK 源码获取通过：${source}`);
  return { source, cachedSource };
}

if (process.argv[1] && existsSync(process.argv[1]) && realpathSync(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    if (process.argv.length !== 4) throw new Error('用法：prepare-local-sdk-source.sh <绝对工作目录> <绝对缓存目录>');
    prepareLocalSource(...process.argv.slice(2));
  } catch (error) { process.stderr.write(error.message + '\n'); process.exitCode = 1; }
}
