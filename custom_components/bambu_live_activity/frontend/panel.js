// Integration-owned, read-only dashboard. No CDN, card install or push dependency.
export function presentation(states, entities, now = Date.now(), locale = 'en', timeZone = 'UTC') {
  const state = key => states[entities[key]];
  const status = state('print_status')?.state ?? 'unknown';
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
    remaining: status === 'running' ? remaining : status === 'pause' ? 'Paused' : '' };
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
        @media (max-width:350px) { main { padding:8px; } ha-card { padding:14px; gap:6px 8px; } }
      </style>
      <main><ha-card aria-label="A1 mini print progress">
        <ha-icon icon="mdi:printer-3d-nozzle" aria-hidden="true"></ha-icon>
        <div class="title"></div><div class="percent"></div>
        <div class="message" role="status"></div><div class="remaining"></div>
        <div class="bar" role="progressbar" aria-label="Print progress" aria-valuemin="0" aria-valuemax="100"><div class="fill"></div></div>
      </ha-card></main>`;
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
  }
}

if (!customElements.get('bambu-live-activity-panel')) {
  customElements.define('bambu-live-activity-panel', BambuLiveActivityPanel);
}
