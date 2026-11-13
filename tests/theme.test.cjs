const { test } = require('node:test');
const assert = require('node:assert/strict');
const { runInNewContext } = require('node:vm');
const { readFileSync } = require('node:fs');
const source = readFileSync(require('node:path').join(__dirname, '../assets/theme.js'), 'utf8');
function theme(saved, storageThrows = false) {
  const handlers = {};
  const data = { 'rallybit-theme': saved };
  const icon = {};
  const label = { textContent: '' };
  const button = { setAttribute() {}, querySelector: selector => selector === 'i' ? icon : label };
  const document = { documentElement: { dataset: {} }, body: {}, querySelectorAll: () => [button], addEventListener: (name, callback) => { handlers[name] = callback; } };
  const window = { matchMedia: () => ({ matches: true }), addEventListener: (name, callback) => { handlers[name] = callback; } };
  const localStorage = { getItem: key => { if (storageThrows) throw new Error('Blocked'); return data[key]; }, setItem: (key, value) => { if (storageThrows) throw new Error('Blocked'); data[key] = value; } };
  runInNewContext(source, { document, window, localStorage, MutationObserver: class { observe() {} } });
  return { document, handlers, data, icon, label };
}
test('light is default even when the OS prefers dark', () => {
  for (const saved of [undefined, null, '', 'invalid', 'light']) assert.equal(theme(saved).document.documentElement.dataset.theme, 'light');
});
test('an explicit dark preference is retained', () => {
  assert.equal(theme('dark').document.documentElement.dataset.theme, 'dark');
});
test('toggle is remembered and updates its label', () => {
  const page = theme();
  page.handlers.click({ target: { closest: () => ({}) } });
  assert.equal(page.document.documentElement.dataset.theme, 'dark');
  assert.equal(page.data['rallybit-theme'], 'dark');
  assert.equal(page.label.textContent, 'Light mode');
  page.handlers.click({ target: { closest: () => ({}) } });
  assert.equal(page.document.documentElement.dataset.theme, 'light');
});
test('clearing the saved preference returns to light across tabs', () => {
  const page = theme('dark');
  page.handlers.storage({ key: 'rallybit-theme', newValue: null });
  assert.equal(page.document.documentElement.dataset.theme, 'light');
});
test('blocked browser storage still allows theme switching', () => {
  const page = theme(null, true);
  assert.equal(page.document.documentElement.dataset.theme, 'light');
  page.handlers.click({ target: { closest: () => ({}) } });
  assert.equal(page.document.documentElement.dataset.theme, 'dark');
});
