// Tests for the browser terminology helper (vertical.js) and for the app's
// inline script still parsing. Run: node --test tests/
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.join(__dirname, '..');
const V = require(path.join(ROOT, 'vertical.js'));
const load = id => JSON.parse(fs.readFileSync(path.join(ROOT, 'verticals', `${id}.json`), 'utf8'));

test('tutor terms match the current app wording', () => {
  V.setVertical(load('tutor'), 'f');
  assert.equal(V.t('client'), 'תלמיד');
  assert.equal(V.t('appointments'), 'שיעורים');
  assert.equal(V.t('calendar_event', { name: 'נועה' }), 'שיעור - נועה');
  assert.equal(V.has('exams'), true);
});

test('barber vertical swaps terms and features', () => {
  V.setVertical(load('barber'), 'm');
  assert.equal(V.t('client'), 'לקוח');
  assert.equal(V.t('appointment'), 'תור');
  assert.equal(V.t('provider'), 'ספר');
  assert.equal(V.has('exams'), false);
  assert.equal(V.has('packages'), true);
});

test('gender picks the matching form; unknown gender falls back to feminine', () => {
  V.setVertical(load('barber'), 'f');
  assert.equal(V.t('provider'), 'ספרית');
  assert.equal(V.g('הוסיפי', 'הוסף'), 'הוסיפי');
  V.setVertical(load('barber'), 'm');
  assert.equal(V.g('הוסיפי', 'הוסף'), 'הוסף');
  V.setVertical(load('barber'), 'zzz');
  assert.equal(V.gender(), 'f');
});

test('placeholders, missing keys and no vertical', () => {
  V.setVertical(load('tutor'), 'f');
  assert.equal(V.t('reminder_one', { name: 'דן', time: '16:00' }), 'שיעור עם דן בשעה 16:00');
  assert.equal(V.t('no_such_term'), 'no_such_term');
  V.setVertical(null, 'f');
  assert.equal(V.t('client'), 'client');
  assert.equal(V.has('exams'), false);
});

test('every vertical has the same term keys and feature flags as tutor', () => {
  const base = load('tutor');
  for (const f of fs.readdirSync(path.join(ROOT, 'verticals')).filter(f => f.endsWith('.json') && !f.startsWith('_'))) {
    const v = load(f.replace('.json', ''));
    assert.deepEqual(Object.keys(v.features).sort(), Object.keys(base.features).sort(), f);
    for (const k of Object.keys(v.terms)) assert.ok(k in base.terms, `${f}: unknown term ${k}`);
  }
});

test("the app's inline script parses", () => {
  const html = fs.readFileSync(path.join(ROOT, 'tutor-app.html'), 'utf8');
  const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
  assert.ok(scripts.length > 0);
  for (const s of scripts) assert.doesNotThrow(() => new Function(s));
});
