import assert from 'node:assert/strict';
import { test } from 'node:test';
globalThis.HTMLElement = class {};
globalThis.customElements = { get() {}, define() {} };
const { presentation } = await import('../custom_components/bambu_live_activity/frontend/panel.js');

const entities = Object.fromEntries(['print_status','print_progress','end_time','remaining_time','model_name'].map(k => [k, k]));
const now = Date.parse('2026-11-06T10:00:00Z');
const states = {
  print_status: { state: 'running' }, print_progress: { state: '47' },
  end_time: { state: '2026-11-06T10:09:00Z' }, remaining_time: { state: '0.15', attributes: { unit_of_measurement: 'h' } },
  model_name: { state: 'cone-customised' },
};

test('same print data, live countdown without a push', () => {
  const result = presentation(states, entities, now, 'en', 'UTC');
  assert.equal(result.title, 'cone-customised');
  assert.equal(result.percent, '47%');
  assert.equal(result.message, '10:09 finish');
  assert.equal(result.remaining, '9m');
  assert.equal(presentation(states, entities, now + 120000, 'en', 'UTC').remaining, '7m');
});

test('unknowns stay readable and finite', () => {
  const result = presentation({}, entities, now, 'en', 'UTC');
  assert.equal(result.title, 'A1 mini');
  assert.equal(result.percent, '—');
  assert.equal(result.progress, 0);
  assert.equal(result.message, 'Printer unavailable');
});

test('pause, completion and error render without controls', () => {
  for (const [status, message] of [['pause','Paused at 47%'],['finish','Finished'],['failed','Print failed'],['idle','Idle']]) {
    assert.equal(presentation({...states, print_status:{state:status}},entities,now,'en','UTC').message, message);
  }
});

test('remaining-time fallback respects units and rejects nonfinite values', () => {
  const fallback = {...states, end_time:{state:'unavailable'}};
  assert.equal(presentation(fallback,entities,now,'en','UTC').remaining,'9m');
  fallback.remaining_time = {state:'Infinity',attributes:{unit_of_measurement:'h'}};
  assert.equal(presentation(fallback,entities,now,'en','UTC').remaining,'—');
});

test('printer text remains data rather than markup', () => {
  const title = '<img src=x onerror=alert(1)>';
  assert.equal(presentation({...states,model_name:{state:title}},entities,now,'en','UTC').title,title);
});
