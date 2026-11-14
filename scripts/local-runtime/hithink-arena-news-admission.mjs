import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import { executeNewsWorker } from './news-admission.mjs';

function loadModule(name, file) {
  const code = readFileSync(new URL(file, import.meta.url), 'utf8');
  return `mod = types.ModuleType('${name}')\nexec(${JSON.stringify(code)}, mod.__dict__)\nsys.modules['${name}'] = mod\n`;
}

export function admissionArgs(args) {
  const [mode, version, digest] = args;
  if (mode === 'apply') {
    assert.equal(args.length, 3, '应用前须使用计划返回的记录版本与完整摘要');
    assert.match(version, /^[1-9]\d*$/);
    assert(Number.isSafeInteger(Number(version)));
    assert.match(digest, /^[a-f0-9]{64}$/);
    return { mode, version: Number(version), digest };
  }
  assert((mode === 'plan' || mode === 'verify') && args.length === 1);
  return { mode };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const plan = admissionArgs(process.argv.slice(2));
  const worker = 'import sys, types\n' +
    loadModule('news_admission_extension', './news_admission_extension.py') +
    loadModule('arena_qualification', './arena-news-qualification.py') +
    readFileSync(new URL('./hithink_arena_news_admission.py', import.meta.url), 'utf8');
  console.log(JSON.stringify(executeNewsWorker(plan, worker), null, 2));
}
