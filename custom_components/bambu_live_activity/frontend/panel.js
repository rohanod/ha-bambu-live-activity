// Integration-owned dashboard using Home Assistant state and entity actions.
export function presentation(states, entities, now = Date.now(), locale = 'en', timeZone = 'UTC') {
  const state = key => states[entities[key]];
  const rawStatus = state('print_status')?.state ?? 'unknown';
  const status = ({ preparing: 'prepare', printing: 'running', paused: 'pause', finished: 'finish' })[rawStatus] ?? rawStatus;
  const validText = value => value && !['unknown', 'unavailable', 'none'].includes(value.toLowerCase());
  const rawProgress = state('print_progress')?.state;
  const number = validText(rawProgress) ? Number(rawProgress) : NaN;
  const progress = Number.isFinite(number) ? Math.max(0, Math.min(100, Math.floor(number))) : 0;
  const percent = Number.isFinite(number) ? `${progress}%` : '—';
  const rawEnd = Date.parse(state('end_time')?.state);
  const end = rawEnd > now && rawEnd - now < 14 * 86400000 ? rawEnd : NaN;
  const rem = state('remaining_time');
  const unit = rem?.attributes?.unit_of_measurement?.toLowerCase();
  let minutes = Number.isFinite(end) ? (end - now) / 60000 :
    Number(rem?.state) * (['h', 'hour', 'hours'].includes(unit) ? 60 : ['s', 'sec', 'seconds'].includes(unit) ? 1 / 60 : 1);
  minutes = Math.ceil(minutes);
  const remaining = Number.isFinite(minutes) && minutes > 0 ?
    (minutes >= 60 ? `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, '0')}m` : `${minutes}m`) : '—';
  const finish = Number.isFinite(end) ? new Intl.DateTimeFormat(locale, {
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZone,
  }).format(end) : '--:--';
  const message = {
    running: `${finish} finish`, pause: `Paused at ${percent}`,
    prepare: 'Preparing…', slicing: 'Preparing…', finish: 'Finished',
    failed: 'Print failed', idle: 'Idle',
  }[status] ?? 'Printer unavailable';
  const name = state('model_name')?.state;
  return { status, title: validText(name) ? name : 'A1 mini', percent, progress, message,
    remaining: ['running', 'pause', 'prepare', 'slicing'].includes(status) ? remaining : '' };
}

class BambuLiveActivityPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    // Only fixed markup enters innerHTML. Entity values always use textContent.
    this.shadowRoot.innerHTML = `
      <style>
        :host { display:block; color:#F2F5F3; font-family:-apple-system,BlinkMacSystemFont,system-ui,sans-serif; }
        main { box-sizing:border-box; width:100%; max-width:552px; padding:12px 16px; margin:0 auto; }
        ha-card { display:grid; grid-template-columns:28px minmax(0,1fr) auto;
          grid-template-areas:'icon title percent' 'icon message remaining' 'bar bar bar';
          gap:6px 10px; padding:16px; border:0; border-radius:22px; box-shadow:none;
          background:var(--activity-background,#0B1A12); color:#F2F5F3; }
        ha-icon { grid-area:icon; align-self:center; color:var(--activity-accent,#3DDC84); --mdc-icon-size:26px; }
        .title { grid-area:title; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
        .title,.percent { font-size:17px; line-height:22px; font-weight:600; }
        .percent { grid-area:percent; text-align:right; }
        .message { grid-area:message; min-width:0; overflow-wrap:anywhere; }
        .remaining { grid-area:remaining; text-align:right; }
        .message,.remaining { font-size:14px; line-height:18px; opacity:.85; }
        .percent,.remaining { font-variant-numeric:tabular-nums; }
        .bar { grid-area:bar; height:5px; margin-top:6px; border-radius:3px; background:#FFFFFF24; overflow:hidden; }
        .fill { height:100%; border-radius:3px; background:var(--activity-accent,#3DDC84); }
        section { margin-top:12px; padding:16px; border-radius:18px; background:var(--card-background-color,#182024); color:var(--primary-text-color,#F2F5F3); }
        section[hidden], button[hidden], label[hidden] { display:none; }
        h2 { font-size:16px; margin:0 0 10px; }
        dl { display:grid; grid-template-columns:1fr 1fr; gap:8px 12px; margin:0; }
        dt { color:var(--secondary-text-color,#AEB8B2); font-size:13px; }
        dd { margin:2px 0 0; overflow-wrap:anywhere; }
        ul { margin:0; padding-inline-start:20px; }
        li + li { margin-top:8px; }
        .actions { display:flex; gap:8px; flex-wrap:wrap; }
        button, select { font:inherit; min-height:44px; padding:6px 12px; border:1px solid var(--divider-color,#67747a);
          border-radius:8px; background:var(--secondary-background-color,#232b30); color:inherit; }
        button:disabled, select:disabled { opacity:.55; }
        button[data-control="stop"] { color:var(--error-color,#ff9b94); }
        label { display:flex; align-items:center; gap:8px; margin-top:10px; }
        .action-result:empty { display:none; }
        @media (max-width:350px) { main { padding:8px; } ha-card { padding:14px; gap:6px 8px; } }
      </style>
      <main><ha-card aria-label="A1 mini print progress">
        <ha-icon icon="mdi:printer-3d-nozzle" aria-hidden="true"></ha-icon>
        <div class="title"></div><div class="percent"></div>
        <div class="message" role="status"></div><div class="remaining"></div>
        <div class="bar" role="progressbar" aria-label="Print progress" aria-valuemin="0" aria-valuemax="100"><div class="fill"></div></div>
      </ha-card>
      <section class="details" hidden><h2>Printer details</h2><dl></dl></section>
      <section class="warnings" hidden><h2>Printer warnings</h2><ul></ul></section>
      <section class="controls" hidden><h2>Printer controls</h2>
        <div class="actions">
          <button type="button" data-control="pause">Pause</button>
          <button type="button" data-control="resume">Resume</button>
          <button type="button" data-control="stop">Stop print</button>
          <button type="button" data-control="chamber_light">Light</button>
        </div>
        <label class="speed">Print speed <select></select></label>
        <p class="action-result" role="status"></p>
      </section></main>`;
    this.shadowRoot.querySelector('.actions').addEventListener('click', event => {
      const key = event.target.closest('button')?.dataset.control;
      if (!key || (key === 'stop' && !window.confirm('Stop this print? It cannot be resumed.'))) return;
      void this._runControl(key);
    });
    this.shadowRoot.querySelector('select').addEventListener('change', event => {
      void this._runControl('speed', event.target.value);
    });
  }

  set hass(value) { this._hass = value; this._render(); }
  set panel(value) { this._panel = value; this._render(); }

  connectedCallback() {
    this._render();
    // Local display clock only: no HA service calls, polling or notifications.
    this._clock = setInterval(() => this._render(), 60000);
  }

  disconnectedCallback() { clearInterval(this._clock); }

  _render() {
    const config = this._panel?.config;
    if (!config || !this._hass) return;
    const view = presentation(this._hass.states, config.entities, Date.now(),
      this._hass.locale?.language || 'en', this._hass.config?.time_zone || 'UTC');
    for (const key of ['title', 'percent', 'message', 'remaining']) {
      const element = this.shadowRoot.querySelector(`.${key}`);
      element.textContent = view[key];
    }
    this.shadowRoot.querySelector('.title').title = view.title;
    const style = config.styles?.[view.status] || config.styles?.running;
    if (style) {
      this.style.setProperty('--activity-accent', style[0]);
      this.style.setProperty('--activity-background', style[1]);
    }
    this.shadowRoot.querySelector('ha-icon').setAttribute('icon', view.status === 'pause' ? 'mdi:printer-pause' : 'mdi:printer-3d-nozzle');
    this.shadowRoot.querySelector('.bar').setAttribute('aria-valuenow', view.progress);
    this.shadowRoot.querySelector('.fill').style.width = `${view.progress}%`;
    this._renderPrinter(config.entities, view.status);
  }
  _renderPrinter(entities, status) {
    const root = this.shadowRoot;
    const states = this._hass.states;
    const state = key => states[entities[key]];
    const value = key => {
      const entry = state(key);
      return entry && !['unknown', 'unavailable', 'none'].includes(entry.state) ? entry.state : '';
    };
    const pretty = text => text.replaceAll('_', ' ');
    const details = [
      ['Stage', pretty(value('stage'))],
      ['Layer', [value('current_layer'), value('total_layers')].filter(Boolean).join(' / ')],
      ['Filament', value('filament')],
      ['AMS', pretty(value('ams_status'))],
      ['Nozzle', value('nozzle_temp') && `${value('nozzle_temp')} ${state('nozzle_temp').attributes.unit_of_measurement ?? ''}`],
      ['Bed', value('bed_temp') && `${value('bed_temp')} ${state('bed_temp').attributes.unit_of_measurement ?? ''}`],
      ['Maintenance', pretty(value('maintenance'))],
    ].filter(([, text]) => text);
    root.querySelector('.details').hidden = !details.length;
    root.querySelector('dl').replaceChildren(...details.map(([label, text]) => {
      const row = document.createElement('div');
      const term = document.createElement('dt');
      const desc = document.createElement('dd');
      term.textContent = label;
      desc.textContent = text;
      row.append(term, desc);
      return row;
    }));

    const errors = state('hms')?.attributes?.errors;
    const warnings = Array.isArray(errors) && value('hms') ? errors.filter(error => error?.code) : [];
    root.querySelector('.warnings').hidden = !warnings.length;
    root.querySelector('ul').replaceChildren(...warnings.map(error => {
      const item = document.createElement('li');
      item.textContent = `${error.code} · ${error.description || error.severity || 'Check printer'}`;
      return item;
    }));

    const active = ['running', 'pause', 'prepare', 'slicing'].includes(status);
    const enabled = { pause: status === 'running', resume: status === 'pause', stop: active, chamber_light: true };
    for (const button of root.querySelectorAll('[data-control]')) {
      const key = button.dataset.control;
      button.hidden = !entities[key];
      button.disabled = Boolean(this._commandPending) || !state(key) || state(key).state === 'unavailable' || !enabled[key];
    }
    const light = root.querySelector('[data-control="chamber_light"]');
    light.setAttribute('aria-pressed', String(value('chamber_light') === 'on'));
    const speed = root.querySelector('select');
    const options = state('speed')?.attributes?.options ?? [];
    root.querySelector('.speed').hidden = !entities.speed;
    if ([...speed.options].map(option => option.value).join() !== options.join()) {
      speed.replaceChildren(...options.map(option => new Option(pretty(option), option)));
    }
    speed.value = value('speed');
    speed.disabled = Boolean(this._commandPending) || !options.length || !value('speed');
    root.querySelector('.controls').hidden = !Object.keys(enabled).some(key => entities[key]) && !entities.speed;
  }

  async _runControl(key, option) {
    const entities = this._panel?.config?.entities ?? {};
    const id = entities[key];
    if (!id || this._commandPending || this._hass.states[id]?.state === 'unavailable') return;
    const result = this.shadowRoot.querySelector('.action-result');
    this._commandPending = true;
    this._render();
    try {
      if (key === 'speed') {
        if (!this._hass.states[id]?.attributes?.options?.includes(option)) return;
        await this._hass.callService('select', 'select_option', { entity_id: id, option });
      } else if (key === 'chamber_light') {
        await this._hass.callService('light', 'toggle', { entity_id: id });
      } else {
        await this._hass.callService('button', 'press', { entity_id: id });
      }
      result.textContent = 'Command sent.';
    } catch {
      result.textContent = 'Command failed. Check the printer connection.';
    } finally {
      this._commandPending = false;
      this._render();
    }
  }

}

if (!customElements.get('bambu-live-activity-panel')) {
  customElements.define('bambu-live-activity-panel', BambuLiveActivityPanel);
}
