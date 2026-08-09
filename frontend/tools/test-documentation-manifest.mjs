import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const root = path.resolve(import.meta.dirname, '../..');
const docsRoot = path.join(root, 'docs');
const manifest = fs.readFileSync(path.join(docsRoot, 'FILE_MANIFEST.md'), 'utf8');

function filesUnder(dir) {
  return fs.readdirSync(dir, {withFileTypes: true}).flatMap((entry) => {
    if (entry.name === '.DS_Store') return [];
    const full = path.join(dir, entry.name);
    return entry.isDirectory() ? filesUnder(full) : [full];
  });
}

const allFiles = filesUnder(docsRoot);
const packageCounts = {
  '01_Product_Architecture': 9,
  '02_System_Architecture': 10,
  '03_UI_System': 10,
  '04_Data_Model': 7,
  '05_Engineering': 9,
  '06_Diagrams': 9,
  '07_Audits': 2,
  'Existing_Project_Docs': 2,
};
const requiredRoot = [
  '00_MASTER_INDEX.md','README.md','CHANGELOG.md','ARCHITECTURE_CHANGELOG.md',
  'DESIGN_SYSTEM.md','FILE_MANIFEST.md','RELEASE_NOTES_v1.6.0.md',
];

const checks = [
  ['recursive documentation total matches manifest', allFiles.length === 65 && manifest.includes('files: **65**')],
  ['every package count matches', Object.entries(packageCounts).every(([dir, count]) => filesUnder(path.join(docsRoot, dir)).length === count)],
  ['all root documentation files are present', requiredRoot.every((name) => fs.existsSync(path.join(docsRoot, name)) && manifest.includes(`\`${name}\``))],
  ['transient metadata is excluded', !allFiles.some((file) => path.basename(file) === '.DS_Store') && manifest.includes('`.DS_Store`')],
  ['manifest avoids volatile byte-size inventory', manifest.includes('Byte sizes') && !manifest.includes('bytes`')],
];

let failed = 0;
for (const [name, ok] of checks) {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}`);
  if (!ok) failed++;
}
if (failed) process.exit(1);
console.log(`\n${checks.length}/${checks.length} documentation manifest checks passed.`);
