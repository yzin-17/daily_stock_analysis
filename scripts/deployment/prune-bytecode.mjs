import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { ownsUpdateLock } from '../../../thesis-ledger-infra/scripts/runtime/lock.mjs';

const args = process.argv.slice(2);
if (args.length > 1 || (args[0] && args[0] !== '--apply')) throw new Error('只支持默认预览或 --apply。');
if (!process.env.INFRA_UPDATE_LOCK_PATH || !ownsUpdateLock(process.env.INFRA_UPDATE_LOCK_PATH)) throw new Error('字节码维护必须经正式持锁入口执行。');
const context = process.env.DOCKER_CONTEXT;
if (!context) throw new Error('Docker context 尚未固定。');
const docker = values => execFileSync('docker', ['--context', context, ...values], { encoding: 'utf8' });
const container = docker(['ps', '--filter', 'label=com.docker.compose.project=thesis-ledger-dev',
  '--filter', 'label=com.docker.compose.service=dsa', '--filter', 'status=running', '--format', '{{.ID}}']).trim();
if (!/^[a-f0-9]{12,64}$/.test(container)) throw new Error('无法唯一确认运行中的开发 DSA 容器。');
if (/running|pending/i.test(docker(['buildx', '--builder', context, 'history', 'ls', '--format', '{{.Status}}']))) throw new Error('存在活动构建，停止字节码维护。');
process.stdout.write(execFileSync('docker', ['--context', context, 'exec', '-i', container, 'python', '-B', '-', ...args], {
  input: readFileSync(new URL('./prune-bytecode.py', import.meta.url)), encoding: 'utf8',
}));
