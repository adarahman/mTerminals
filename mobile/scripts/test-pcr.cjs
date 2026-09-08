const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const { test } = require('node:test');

const context = { exports: {} };
vm.runInNewContext(ts.transpileModule(
  fs.readFileSync(path.join(__dirname, '../src/utils/pcr.ts'), 'utf8'),
  { compilerOptions: { module: ts.ModuleKind.CommonJS } },
).outputText, context);
const { formatPcrChange } = context.exports;

test('PCR change measures ratio movement, including when calls unwind', () => {
  assert.equal(formatPcrChange([
    { ceOI: 1000, peOI: 1188, ceChgOI: -100, peChgOI: 88 },
  ]), '+0.188');
  assert.equal(formatPcrChange([
    { ceOI: 1000, peOI: 812, ceChgOI: 0, peChgOI: -188 },
  ]), '-0.188');
});

test('missing data and undefined ratios remain unavailable', () => {
  assert.equal(formatPcrChange([]), '—');
  assert.equal(formatPcrChange([{ ceOI: 100, peOI: 100 }]), '—');
  assert.equal(formatPcrChange([
    { ceOI: 100, peOI: 100, ceChgOI: 100, peChgOI: 50 },
  ]), '—');
});
