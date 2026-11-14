import { execFileSync, spawn } from 'node:child_process';
import { readFileSync } from 'node:fs';

const mode = process.argv[2], container = 'thesis-ledger-dev-dsa-1';
if (process.argv.length !== 3 || !['snapshot-state', 'archive-state'].includes(mode)) throw new Error('未知应用状态导出命令。');
const labels = execFileSync('docker', ['inspect', '--format', '{{index .Config.Labels "com.docker.compose.project"}}/{{index .Config.Labels "com.docker.compose.service"}}', container], { encoding: 'utf8' }).trim();
if (labels !== 'thesis-ledger-dev/dsa') throw new Error('应用状态导出目标不匹配。');
const args = mode === 'snapshot-state'
  ? ['exec', '-i', container, 'python', '-']
  : ['exec', container, 'tar', '-C', '/app/data', '--exclude=./stock_analysis.db',
    '--exclude=./stock_analysis.db-wal', '--exclude=./stock_analysis.db-shm', '-cf', '-', '.'];
// tar 直接流向调用方的私有文件，不增加归档大小或内存缓冲上限。
const child = spawn('docker', args, { stdio: ['pipe', 'inherit', 'inherit'] });
child.stdin.on('error', error => { if (error.code !== 'EPIPE') child.kill(); });
child.on('error', () => { process.stderr.write('应用状态导出进程启动失败。\n'); process.exitCode = 1; });
child.on('close', code => { process.exitCode = code ?? 1; });
child.stdin.end(mode === 'snapshot-state' ? readFileSync(new URL('./snapshot-state.py', import.meta.url)) : undefined);
