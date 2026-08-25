import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const root = path.resolve(import.meta.dirname, '../..');
const read = (rel) => fs.readFileSync(path.join(root, rel), 'utf8');
const workflow = read('.github/workflows/ci.yml');
const build = read('frontend/build.mjs');
const packageJson = JSON.parse(read('frontend/package.json'));
const pyproject = read('src/pyproject.toml');
const logging = read('src/logging_config.py');
const readiness = read('src/operational_readiness.py');
const gitignore = read('.gitignore');
const engineeringDir = path.join(root, 'docs/05_Engineering');
const docs = fs.readdirSync(engineeringDir).filter((name) => name.endsWith('.md'))
  .map((name) => read(`docs/05_Engineering/${name}`));

const checks = [
  ['all nine engineering documents record implementation status', docs.length === 9 && docs.every((doc) => doc.includes('Status:** Implemented'))],
  ['frontend production build is a release gate', workflow.includes('npm run build') && build.includes('process.exit(1)')],
  ['all engineering contracts run in CI', workflow.includes('npm run test:engineering') && packageJson.scripts['test:engineering']],
  ['backend syntax and undefined names are lint-gated', workflow.includes('ruff check . --select E9,F63,F7,F82') && pyproject.includes('ruff>=')],
  ['backend regression suite runs in CI', workflow.includes('python -m pytest')],
  ['browser regression suite runs in CI', workflow.includes('npm run test:e2e')],
  ['supply-chain audits remain blocking', workflow.includes('npm audit --audit-level=high') && workflow.includes('pip_audit')],
  ['runtime readiness provides preflight and smoke', readiness.includes('preflight') && readiness.includes('smoke_health')],
  ['structured logging redacts secrets', logging.includes('RedactSensitiveHeaders') && logging.includes('StructuredFormatter')],
  ['generated and sensitive artifacts stay untracked', ['dist','*.log','.env','runtime/**'].every((entry) => gitignore.includes(entry))],
  ['version guidance includes the current v1.6.0 release', docs.some((doc) => doc.includes('v1.6.0'))],
];

let failed = 0;
for (const [name, ok] of checks) {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}`);
  if (!ok) failed++;
}
if (failed) process.exit(1);
console.log(`\n${checks.length}/${checks.length} engineering contract checks passed.`);
