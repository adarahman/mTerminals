import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

const root = path.resolve(import.meta.dirname, '../..');
const workflow = fs.readFileSync(path.join(root, '.github/workflows/ci.yml'), 'utf8');
const dependabot = fs.readFileSync(path.join(root, '.github/dependabot.yml'), 'utf8');

const actionUses = [...workflow.matchAll(/uses:\s*([^\s#]+)/g)].map(match => match[1]);
assert.ok(actionUses.length >= 7, 'expected every external action use to be visible to the contract');
for (const action of actionUses) {
  assert.match(action, /^[\w.-]+\/[\w.-]+@[0-9a-f]{40}$/, `${action} must use an immutable full SHA`);
}
assert.doesNotMatch(workflow, /uses:\s*[^\s#]+@v\d+/, 'floating major action tags are forbidden');
assert.match(workflow, /npm audit --audit-level=high/);
assert.match(workflow, /python -m pip_audit --local/);
assert.match(workflow, /python -m pip check/);
for (const ecosystem of ['github-actions', 'npm', 'pip']) {
  assert.ok(dependabot.includes(`package-ecosystem: ${ecosystem}`), `Dependabot must cover ${ecosystem}`);
}

console.log('PASS  All external GitHub Actions are pinned to full commit SHAs');
console.log('PASS  Frontend and backend vulnerability audits are CI gates');
console.log('PASS  Dependabot covers Actions, npm, and Python dependencies');
console.log('\n3/3 CI supply-chain checks passed.');
