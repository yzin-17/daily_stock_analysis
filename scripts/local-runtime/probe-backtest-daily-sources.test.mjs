import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';

for (const container of ['abcdef123456', '', 'abcdef123456\n123456abcdef']) {
  test(`诊断只针对唯一开发 DSA：${JSON.stringify(container)}`, () => {
    const root = mkdtempSync(join(tmpdir(), 'source-probe-test-'));
    try {
      const log = join(root, 'calls');
      writeFileSync(join(root, 'docker'), `#!/bin/sh
if [ "$1" = ps ]; then
  printf '%s' "$TEST_CONTAINER"
else
  printf '%s\\n' "$@" > "$TEST_LOG"
  cat > "$TEST_LOG.stdin"
fi
`, { mode: 0o700 });
      const args = ['--symbol', '510300.SH', '--asset', 'ETF', '--start', '2026-09-24',
        '--end', '2026-11-10', '--target', 'efinance/eastmoney'];
      const result = spawnSync('bash', ['scripts/local-runtime/probe-backtest-daily-sources.sh', ...args], {
        cwd: new URL('../..', import.meta.url), encoding: 'utf8',
        env: { ...process.env, PATH: `${root}:${process.env.PATH}`, TEST_CONTAINER: container, TEST_LOG: log },
      });
      if (container === 'abcdef123456') {
        assert.equal(result.status, 0, result.stderr);
        assert.deepEqual(readFileSync(log, 'utf8').trim().split('\n'),
          ['exec', '-i', '-w', '/app', '-e', 'PYTHONDONTWRITEBYTECODE=1', container, 'python', '-B', '-c',
            'import sys; exec(compile(sys.stdin.read(), "<backtest-source-probe>", "exec"))', ...args]);
        const script = readFileSync(`${log}.stdin`, 'utf8');
        assert.match(script, /isolated_runtime/);
        assert.match(script, /mode=ro/);
      } else {
        assert.equal(result.status, 1);
        assert.match(result.stderr, /无法唯一确认/);
      }
    } finally { rmSync(root, { recursive: true, force: true }); }
  });
}
