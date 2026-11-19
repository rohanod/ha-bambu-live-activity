import assert from 'node:assert/strict';
import { test } from 'node:test';
globalThis.HTMLElement = class {};
let Panel;
globalThis.customElements = { get() {}, define(_name, implementation) { Panel = implementation; } };
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

test('Bambuddy paused state shows minutes and layer controls can target its entities', async () => {
  const paused = { ...states, print_status: { state: 'paused' }, end_time: { state: 'unknown' } };
  const view = presentation(paused, entities, now, 'en', 'UTC');
  assert.equal(view.status, 'pause');
  assert.equal(view.remaining, '9m');

  const calls = [];
  const panel = Object.create(Panel.prototype);
  panel._panel = { config: { entities: { resume: 'button.renamed_resume', speed: 'select.renamed_speed', chamber_light: 'light.printer' } } };
  panel._hass = {
    states: { 'button.renamed_resume': { state: 'unknown' }, 'select.renamed_speed': { state: 'normal', attributes: { options: ['normal', 'silent'] } }, 'light.printer': { state: 'on' } },
    callService: async (...args) => calls.push(args),
  };
  panel._render = () => {};
  panel.shadowRoot = { querySelector: () => ({}) };
  await panel._runControl('resume');
  await panel._runControl('speed', 'invalid');
  await panel._runControl('speed', 'silent');
  await panel._runControl('chamber_light');
  assert.deepEqual(calls, [
    ['button', 'press', { entity_id: 'button.renamed_resume' }],
    ['select', 'select_option', { entity_id: 'select.renamed_speed', option: 'silent' }],
    ['light', 'toggle', { entity_id: 'light.printer' }],
  ]);
});
