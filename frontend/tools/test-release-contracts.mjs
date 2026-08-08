import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const root = path.resolve(import.meta.dirname, '../..');
const read = (rel) => fs.readFileSync(path.join(root, rel), 'utf8');
const version = read('VERSION').trim();
const rootReadme = read('README.md');
const docsReadme = read('docs/README.md');
const index = read('docs/00_MASTER_INDEX.md');
const changelog = read('docs/CHANGELOG.md');
const notes = read(`docs/RELEASE_NOTES_v${version}.md`);
const backendReadme = read('backend/README.md');
const workflow = read('.github/workflows/ci.yml');
const packageJson = JSON.parse(read('frontend/package.json'));

const suites = [
  'option-chain','strike-detail','capital-flow','decision','scenario',
  'paper-trading','price-chart','design-system','health-status','feed-recovery',
  'ci-supply-chain','system-architecture','ui-system','data-model','engineering',
  'diagrams','audits','release','manifest',
];
const checks = [
  ['release marker is semantic v1.6.0', version === '1.6.0'],
  ['root README links every architecture package', ['01_Product_Architecture','02_System_Architecture','03_UI_System','04_Data_Model','05_Engineering','06_Diagrams','07_Audits'].every((x) => rootReadme.includes(x))],
  ['entry points agree on release version', [rootReadme, docsReadme, index, changelog, notes].every((doc) => doc.includes('1.6.0'))],
  ['release notes include verification and operations', notes.includes('Verification baseline') && notes.includes('Upgrade and operations') && notes.includes('205 backend tests')],
  ['released changelog retains v1.6.0 and one current development section', changelog.includes('mTerminals v1.6.0 architecture conformance') && (changelog.match(/^## Unreleased/gm) || []).length <= 1],
  ['backend README reflects the real suite', backendReadme.includes('205 deterministic tests') && !backendReadme.includes('starting skeleton')],
  ['all frontend contract suites are named in CI', suites.every((suite) => workflow.includes(`npm run test:${suite}`))],
  ['release script is registered', packageJson.scripts['test:release'] === 'node tools/test-release-contracts.mjs'],
  ['three release jobs remain present', ['frontend:','backend:','browser-e2e:'].every((job) => workflow.includes(job))],
  ['release safety states paper is default', rootReadme.includes('Paper trading is the default') && notes.includes('Paper trading remains the default')],
];

let failed = 0;
for (const [name, ok] of checks) {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}`);
  if (!ok) failed++;
}
if (failed) process.exit(1);
console.log(`\n${checks.length}/${checks.length} release contract checks passed.`);
