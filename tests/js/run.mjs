// Minimal test runner: finds *.test.mjs beside this file, runs each exported
// test, reports pass/fail. No framework, no dependencies — node only.
import { readdirSync } from 'node:fs';
import { pathToFileURL, fileURLToPath } from 'node:url';
import path from 'node:path';

const dir = path.dirname(fileURLToPath(import.meta.url));
const files = readdirSync(dir).filter(f => f.endsWith('.test.mjs'));

let passed = 0;
const failures = [];

for (const file of files) {
  const mod = await import(pathToFileURL(path.join(dir, file)).href);
  for (const [name, fn] of Object.entries(mod)) {
    if (typeof fn !== 'function') continue;
    try {
      await fn();
      passed++;
      console.log(`  ok   ${file} :: ${name}`);
    } catch (err) {
      failures.push({ file, name, err });
      console.log(`  FAIL ${file} :: ${name}`);
    }
  }
}

console.log(`\n${passed} passed, ${failures.length} failed`);
for (const f of failures) {
  console.log(`\n--- ${f.file} :: ${f.name} ---\n${f.err && f.err.stack || f.err}`);
}
process.exit(failures.length ? 1 : 0);
