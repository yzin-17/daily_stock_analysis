import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import { executeNewsWorker } from './news-admission.mjs';
import { admissionArgs } from './hithink-arena-news-admission.mjs';

function loadModule(name, file) {
  return `mod = types.ModuleType('${name}')\nexec(${JSON.stringify(readFileSync(new URL(file, import.meta.url), 'utf8'))}, mod.__dict__)\nsys.modules['${name}'] = mod\n`;
}

export function main(args = process.argv.slice(2)) {
  const plan = admissionArgs(args);
  const worker = 'import sys, types\n' +
    loadModule('news_admission_extension', './news_admission_extension.py') +
    loadModule('arena_qualification', './arena-news-qualification.py') +
    readFileSync(new URL('./hithink_news_pagination_admission.py', import.meta.url), 'utf8');
  return executeNewsWorker(plan, worker);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  console.log(JSON.stringify(main(), null, 2));
}
