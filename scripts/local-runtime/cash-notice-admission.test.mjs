import { test } from 'node:test';
import assert from 'node:assert/strict';
import { main, parseNoticeAdmissionArguments } from './cash-notice-admission.mjs';
const confirm = '--confirm=thesis-ledger-dev/510300.SH/2023-01-03/2023-02-01';
const hash = '--sha256=924ca0ef585cbc044a34cc552ec0a17b24645a63ecbfec4df9fa01ded414cfa3';
test('缺省只返回精确有界计划，无 Docker 操作', () => {
  assert.equal(main([confirm, hash]).mode, 'plan');
  assert.equal(parseNoticeAdmissionArguments([confirm, hash, '--apply']).apply, true);
});
test('拒绝缺摘要、未知来源、扩大范围、重复选项', () => {
  for (const args of [[confirm], [confirm, '--sha256=' + 'a'.repeat(64)], [confirm.replace('510300', '159516'), hash], [confirm, hash, '--apply', '--apply']]) assert.throws(() => parseNoticeAdmissionArguments(args));
});
