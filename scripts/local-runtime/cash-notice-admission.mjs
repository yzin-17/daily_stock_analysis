import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdirSync, rmSync, rmdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const project = 'thesis-ledger-dev';
const reviewedDigest = '924ca0ef585cbc044a34cc552ec0a17b24645a63ecbfec4df9fa01ded414cfa3';
export function parseNoticeAdmissionArguments(args) {
  assert.deepEqual(args.filter((arg) => !arg.startsWith('--sha256=')), [`--confirm=${project}/510300.SH/2023-01-03/2023-02-01`, ...(args.includes('--apply') ? ['--apply'] : [])]);
  const digest = args.find((arg) => arg.startsWith('--sha256='))?.slice(9);
  assert.equal(args.filter((arg) => arg.startsWith('--sha256=')).length, 1);
  assert.equal(digest, reviewedDigest, '需确认已核验的精确公告来源包摘要');
  return { digest, apply: args.includes('--apply') };
}
export function main(args = process.argv.slice(2)) {
  const { digest, apply } = parseNoticeAdmissionArguments(args);
  if (!apply) return { mode: 'plan', project, digest, symbol: '510300.SH', range: { start: '2023-01-03', end: '2023-02-01' }, validHours: 24 };
  assert(!process.env.DOCKER_HOST && !process.env.BUILDX_BUILDER);
  const docker = (...argv) => execFileSync('docker', argv, { encoding: 'utf8', timeout: 30_000, maxBuffer: 100_000 }).trim();
  assert.equal(docker('context', 'show'), 'desktop-linux');
  const endpoint = docker('context', 'inspect', 'desktop-linux', '--format', '{{.Endpoints.docker.Host}}');
  assert(endpoint.startsWith('unix://'));
  const checksum = execFileSync('cksum', { input: endpoint, encoding: 'utf8' }).split(/\s+/)[0];
  const lock = join(tmpdir(), `thesis-ledger-update-${process.getuid()}-${checksum}.lock`);
  mkdirSync(lock);
  try {
    const container = `${project}-dsa-1`;
    const inspected = JSON.parse(docker('inspect', container))[0];
    assert.equal(inspected.Config.Labels['com.docker.compose.project'], project);
    assert.equal(inspected.Config.Labels['com.docker.compose.service'], 'dsa');
    assert.equal(inspected.State.Running, true);
    assert(inspected.Mounts.some((mount) => mount.Destination === '/app/data' && mount.Type === 'volume'));
    const code = `import json, sqlite3\nfrom pathlib import Path\nfrom datetime import datetime, timedelta, timezone\nfrom hashlib import sha256\nfrom src.services.thesis_ledger_control import ThesisLedgerControlStore, _database_path\nfrom src.services.thesis_ledger_notice_dividends import DATA_PATH, ROUTE_KEY, ROUTE_TARGET, OfficialNoticeDividendProvider, notice_dividend_revisions\nnow = datetime.now(timezone.utc)\nraw = DATA_PATH.read_bytes()\ndigest = sha256(raw).hexdigest()\nassert digest == ${JSON.stringify(digest)}\nobservation = OfficialNoticeDividendProvider().read('510300.SH', '2023-01-03', '2023-02-01', now.isoformat())\nassert observation['coverage']['complete'] and len(observation['facts']) == 1\nassert observation['facts'][0]['cashAmount'] == '0.064'\ndb = Path(_database_path()).resolve()\nassert str(db) == '/app/data/stock_analysis.db' and db.is_file()\nconnection = sqlite3.connect(f'{db.as_uri()}?mode=ro', uri=True)\ntry:\n    assert connection.execute(\"SELECT name FROM sqlite_master WHERE type='table' AND name='thesis_ledger_route_admission_v3'\").fetchone()\nfinally:\n    connection.close()\nevidence = db.parent / 'thesis-ledger-route-evidence' / (digest + '.json')\nevidence.parent.mkdir(exist_ok=True)\nif evidence.exists():\n    assert evidence.read_bytes() == raw\nelse:\n    with evidence.open('xb') as output:\n        output.write(raw)\nassert sha256(evidence.read_bytes()).hexdigest() == digest\nrevisions = notice_dividend_revisions()\nstore = ThesisLedgerControlStore(database_path=str(db))\nrecord = store.record_route_admission_v3(key=ROUTE_KEY, target=ROUTE_TARGET, evidence_ref='sha256:' + digest, evidence_sha256=digest, scope_symbols=['510300.SH'], scope_date_from='2023-01-03', scope_date_to='2023-02-01', adapter_revision=revisions['adapterRevision'], source_revision=revisions['sourceRevision'], credential_revision=revisions['credentialRevision'], valid_from=now.isoformat(), valid_until=(now + timedelta(hours=24)).isoformat(), recorded_by='m31-reviewed-notice-research')\nprint(json.dumps({'status': 'admitted', 'digest': digest, 'evidencePath': str(evidence), 'validUntil': record['validUntil'], 'sourceRevision': revisions['sourceRevision']}, ensure_ascii=False))\n`;
    return JSON.parse(execFileSync('docker', ['exec', '-i', '--user', 'dsa:dsa', container, 'python', '-'], { input: code, encoding: 'utf8', timeout: 30_000, maxBuffer: 100_000 }));
  } finally {
    rmSync(join(lock, 'pid'), { force: true });
    rmdirSync(lock);
  }
}
if (import.meta.url === pathToFileURL(process.argv[1]).href) console.log(JSON.stringify(main(), null, 2));
