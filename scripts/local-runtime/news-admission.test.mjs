import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { newsAdmissionPlan, newsTargetIdentity, newsAdmissionWorker } from './news-admission.mjs';
import { admissionArgs } from './hithink-arena-news-admission.mjs';
import { main as paginationAdmission } from './hithink-news-pagination-admission.mjs';

const feed = Buffer.from('{"items":[]}');
const args = (source = 'yahoo-search', confirmation = 'yfinance/NVDA.US/public-metadata-research') => [
  `--source=${source}`, `--confirm=thesis-ledger-dev/${confirmation}`,
  '--feed=/synthetic/feed.json', `--sha256=${createHash('sha256').update(feed).digest('hex')}`,
  '--observed-at=2026-10-22T00:00:00Z', '--start=2026-10-19', '--end=2026-10-29',
  '--valid-hours=168', '--recorded-by=offline-contract-test'];
test('市场快讯分别确认固定来源与市场范围，不接受证券代码替代', () => {
  for (const [source, provider] of [['cls-roll', 'cls'], ['wallstreetcn-a-stock', 'wallstreetcn']]) {
    const scoped = args(source, `${provider}/CN.STOCK/public-metadata-research`);
    const plan = newsAdmissionPlan(scoped, () => feed);
    assert.equal(plan.providerId, provider); assert.equal(plan.symbol, 'CN.STOCK');
    assert.throws(() => newsAdmissionPlan(scoped.map(arg => arg.replace('/CN.STOCK/', '/600519.SH/')), () => feed));
  }
});
test('默认纯计划，不含上游请求或写操作', () => {
  const plan = newsAdmissionPlan(args(), () => feed);
  assert.equal(plan.mode, 'plan'); assert.equal(plan.symbol, 'NVDA.US');
  assert.equal(plan.license, 'public-metadata-research'); assert.equal(plan.validHours, 168);
  assert.equal(newsAdmissionPlan([...args(), '--verify'], () => feed).mode, 'verify');
  assert.throws(() => newsAdmissionPlan([...args(), '--verify', '--apply'], () => feed));
});
test('拒绝范围、摘要、大小、有效期、重复和缺值参数', () => {
  for (const change of [x => x.slice(1), x => [...x, '--apply', '--apply'], x => [...x, '--force'],
    x => x.map(y => y.replace('/NVDA.US/', '/MSFT.US/')), x => x.map(y => y.replace('=168', '=169')),
    x => x.map(y => y.replace('offline-contract-test', '')), x => x.filter(y => !y.startsWith('--sha256'))]) {
    assert.throws(() => newsAdmissionPlan(change(args()), () => feed));
  }
  assert.throws(() => newsAdmissionPlan(args(), () => Buffer.from('changed')));
  assert.throws(() => newsAdmissionPlan(args(), () => Buffer.alloc(512 * 1024 + 1)));
});
test('必须显式选择已审阅来源，非法选择在读取证据前拒绝', () => {
  const read = () => assert.fail('来源拒绝前不应读取证据');
  const withoutSource = args().filter(arg => !arg.startsWith('--source='));
  for (const input of [withoutSource, [...withoutSource, '--source='],
    [...withoutSource, '--source=unreviewed'], [...args(), '--source=yahoo-search']]) {
    assert.throws(() => newsAdmissionPlan(input, read), /--source|未审阅/);
  }
});
test('只允许正确、健康、可写的原目标持久卷', () => {
  const container = () => ({ Name: '/thesis-ledger-dev-dsa-1', Id: 'a'.repeat(64), Image: `sha256:${'b'.repeat(64)}`,
    Config: { Labels: { 'com.docker.compose.project': 'thesis-ledger-dev', 'com.docker.compose.service': 'dsa' } },
    State: { Running: true, Health: { Status: 'healthy' } },
    Mounts: [{ Type: 'volume', Name: 'thesis-ledger-dsa-data', Destination: '/app/data', RW: true }] });
  assert.equal(newsTargetIdentity(container()).id, 'a'.repeat(64));
  const reordered = container();
  reordered.Mounts.push({ Type: 'volume', Name: 'logs', Source: '/logs', Destination: '/app/logs', RW: true });
  const same = structuredClone(reordered); same.Mounts.reverse();
  assert.deepEqual(newsTargetIdentity(reordered), newsTargetIdentity(same));
  for (const change of [x => x.Name = '/other', x => x.State.Running = false,
    x => x.State.Health.Status = 'unhealthy', x => x.Mounts[0].RW = false,
    x => x.Mounts[0].Name = 'other', x => x.Config.Labels['com.docker.compose.service'] = 'worker']) {
    const value = container(); change(value); assert.throws(() => newsTargetIdentity(value));
  }
});
test('现有 AKShare 来源必须使用自己的精确确认，其他来源拒绝', () => {
  const scoped = args('akshare-eastmoney', 'akshare/600519.SH/public-metadata-research');
  const plan = newsAdmissionPlan(scoped, () => feed);
  assert.equal(plan.providerId, 'akshare'); assert.equal(plan.symbol, '600519.SH');
  assert.equal(plan.license, 'public-metadata-research'); assert.equal(plan.mode, 'plan');
  assert.throws(() => newsAdmissionPlan(args('akshare-eastmoney'), () => feed));
  assert.throws(() => newsAdmissionPlan(args('unreviewed'), () => feed));
  assert.throws(() => newsAdmissionPlan([...scoped, '--source=akshare-eastmoney', '--source=akshare-eastmoney'], () => feed));
});
test('HiThink 首次准入只接受已审阅的账号元数据配置档', () => {
  const scoped = args('hithink-timeline', 'hithink/600519.SH/account-news-metadata');
  const plan = newsAdmissionPlan(scoped, () => feed);
  assert.equal(plan.providerId, 'hithink'); assert.equal(plan.symbol, '600519.SH');
  assert.equal(plan.license, 'account-news-metadata'); assert.equal(plan.mode, 'plan');
  assert.throws(() => newsAdmissionPlan(args('hithink-timeline'), () => feed));
});
test('Yahoo 搜索只登记固定 NVDA 元数据，拒绝误用其他来源确认', () => {
  const scoped = args();
  const plan = newsAdmissionPlan(scoped, () => feed);
  assert.equal(plan.providerId, 'yfinance'); assert.equal(plan.symbol, 'NVDA.US');
  assert.equal(plan.license, 'public-metadata-research'); assert.equal(plan.mode, 'plan');
  assert.throws(() => newsAdmissionPlan(args('yahoo-search', 'hithink/600519.SH/account-news-metadata'), () => feed));
  assert.throws(() => newsAdmissionPlan(scoped.map(arg => arg.replace('/NVDA.US/', '/AAPL.US/')), () => feed));
});
const extensionArgs = () => [...args('akshare-eastmoney', 'akshare/600519.SH/public-metadata-research')
  .filter(arg => !arg.startsWith('--valid-hours=')),
  '--extend-existing', '--expected-record-version=1',
  `--expected-record-sha256=${'c'.repeat(64)}`];
test('既有范围扩展须显式绑定旧版本及摘要，不接受新的有效期', () => {
  const plan = newsAdmissionPlan(extensionArgs(), () => feed);
  assert.equal(plan.extendExisting, true); assert.equal(plan.expectedRecordVersion, 1);
  assert.equal(plan.expectedRecordSha256, 'c'.repeat(64)); assert.equal(plan.validHours, null);
  assert.equal(newsAdmissionPlan([...extensionArgs(), '--verify'], () => feed).mode, 'verify');
  for (const change of [
    x => [...x, '--valid-hours=168'], x => x.filter(arg => !arg.startsWith('--expected-record-version=')),
    x => x.filter(arg => !arg.startsWith('--expected-record-sha256=')),
    x => x.map(arg => arg.replace('--expected-record-version=1', '--expected-record-version=0')),
    x => x.map(arg => arg.replace('--expected-record-version=1', '--expected-record-version=1.5')),
    x => x.filter(arg => arg !== '--extend-existing'),
  ]) assert.throws(() => newsAdmissionPlan(change(extensionArgs()), () => feed));
});
test('范围扩展仅开放两条既有中国个股新闻配置档', () => {
  const hithink = extensionArgs().map(arg => arg.replace('--source=akshare-eastmoney', '--source=hithink-timeline')
    .replace('akshare/600519.SH/public-metadata-research', 'hithink/600519.SH/account-news-metadata'));
  assert.equal(newsAdmissionPlan(hithink, () => feed).providerId, 'hithink');
  const foreign = [...args().filter(arg => !arg.startsWith('--valid-hours=')), '--extend-existing',
    '--expected-record-version=1', `--expected-record-sha256=${'c'.repeat(64)}`];
  assert.throws(() => newsAdmissionPlan(foreign, () => feed));
});
test('正式 Docker 执行程序包含可导入的受控更新模块', () => {
  const result = spawnSync('python3', ['-B', '-c', `__name__ = 'news_worker'\n${newsAdmissionWorker()}\nassert callable(extend_existing_news_admission)`],
    { encoding: 'utf8', timeout: 10_000 });
  assert.equal(result.status, 0, result.stderr);
});
test('两个同花顺维护入口可导入通用执行模块且不执行维护', () => {
  assert.deepEqual(admissionArgs(['plan']), { mode: 'plan' });
  assert.deepEqual(admissionArgs(['verify']), { mode: 'verify' });
  assert.equal(typeof paginationAdmission, 'function');
});
test('过期续验仅接受同花顺、一小时和精确旧记录确认', () => {
  const renewal = extensionArgs().map(arg => arg.replace('--source=akshare-eastmoney', '--source=hithink-timeline')
    .replace('akshare/600519.SH/public-metadata-research', 'hithink/600519.SH/account-news-metadata')
    .replace('--extend-existing', '--renew-existing'));
  const input = [...renewal, '--valid-hours=1'];
  assert.equal(newsAdmissionPlan(input, () => feed).renewExisting, true);
  assert.equal(newsAdmissionPlan(input, () => feed).validHours, 1);
  for (const change of [x => [...x, '--extend-existing'], x => x.map(a => a.replace('--valid-hours=1', '--valid-hours=2')),
    x => x.filter(a => !a.startsWith('--valid-hours=')), x => x.filter(a => !a.startsWith('--expected-record-sha256=')),
    x => x.map(a => a.replace('hithink-timeline', 'akshare-eastmoney'))]) {
    assert.throws(() => newsAdmissionPlan(change(input), () => feed));
  }
  assert(newsAdmissionWorker().includes('renew_existing_news_admission'));
});
test('最近七日向前滚动必须显式附属于同花顺过期续验', () => {
  const input = [...extensionArgs().map(arg => arg.replace('--source=akshare-eastmoney', '--source=hithink-timeline')
    .replace('akshare/600519.SH/public-metadata-research', 'hithink/600519.SH/account-news-metadata')
    .replace('--extend-existing', '--renew-existing')), '--valid-hours=1', '--roll-recent-window'];
  assert.equal(newsAdmissionPlan(input, () => feed).rollRecentWindow, true);
  assert(!Object.hasOwn(newsAdmissionPlan(input.filter(arg => arg !== '--roll-recent-window'), () => feed), 'rollRecentWindow'));
  for (const change of [x => x.filter(arg => arg !== '--renew-existing'),
    x => x.map(arg => arg.replace('--renew-existing', '--extend-existing')),
    x => [...x, '--roll-recent-window'], x => x.map(arg => arg.replace('--valid-hours=1', '--valid-hours=2'))]) {
    assert.throws(() => newsAdmissionPlan(change(input), () => feed));
  }
});
