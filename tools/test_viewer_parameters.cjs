const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

let assertions = 0;
for (const [file, helper] of [['app.js', 'numberParam'], ['overview.js', 'numericParam'], ['physical.js', 'numberParam']]) {
  const source = fs.readFileSync(path.join(__dirname, '../asset_simulation/viewer/js', file), 'utf8');
  function extract(name) {
    const match = source.match(new RegExp('function ' + name + '\\([^]*?\\n\\}'));
    assert.ok(match, `${file}: missing ${name}`);
    return match[0];
  }
  const state = {};
  const ctx = vm.createContext({URLSearchParams, state, location: {search: ''}});
  vm.runInContext(extract('finiteNumberOr') + '\n' + extract(helper) + '\n' + extract('readUrl'), ctx);
  vm.runInContext('readUrl()', ctx);
  assert.equal(state.seed, 42, `${file}: missing seed should default to 42`);
  assert.equal(state.years, 60, `${file}: missing horizon should default to 60`);
  assert.equal(state.initialYear, 2030);
  assertions += 3;
  ctx.location.search = '?seed=0&years=20&year=2035&month=2';
  vm.runInContext('readUrl()', ctx);
  assert.equal(state.seed, 0, `${file}: zero seed must not become 42`);
  assert.equal(state.years, 20);
  assertions += 2;
  ctx.location.search = '?seed=&years=NaN';
  vm.runInContext('readUrl()', ctx);
  assert.equal(state.seed, 42);
  assert.equal(state.years, 60);
  assertions += 2;
  assert.equal(vm.runInContext('finiteNumberOr("0", 42)', ctx), 0);
  assert.ok(!source.includes('Number($("seedInput").value) || 42'), `${file}: submit still rejects zero`);
  assertions += 2;
}
console.log(`Viewer parameter regression: ${assertions} assertions passed across three views.`);
