'use strict';

const state = {
  events: [],
  meta: null,
  cadence: null,
  lookup: null,          // loaded on demand; it is the largest file
  services: {},
  apis: {},
  apiDays: null,
  details: new Map(),
  tryIts: [],
  requestFormat: 'curl',
  tiers: new Set([0, 1]),
  days: 30,
  filter: '',
  showRemoved: false,
};

const $ = (sel) => document.querySelector(sel);
const WEEKDAY = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];

/* Dates in the index are plain YYYY-MM-DD. Parsing those with `new Date()`
   treats them as UTC and then renders in local time, which shifts the day for
   anyone west of Greenwich. Build a local date explicitly instead. */
function parseDate(iso) {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(y, m - 1, d);
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

async function loadJSON(name) {
  const res = await fetch(name, { cache: 'no-cache' });
  if (!res.ok) throw new Error(`${name}: ${res.status} ${res.statusText}`);
  return res.json();
}

/* ---------------- boot ---------------- */

async function boot() {
  try {
    const [events, meta, cadence, services, apis] = await Promise.all([
      loadJSON('events.json'),
      loadJSON('meta.json'),
      loadJSON('cadence.json'),
      loadJSON('services.json').catch(() => ({ rolesByService: {} })),
      loadJSON('apis.json').catch(() => ({ services: {}, exploredDays: null })),
    ]);
    state.events = events.events;
    state.meta = meta;
    state.cadence = cadence;
    state.services = services.rolesByService || {};
    state.apis = apis.services || {};
    state.apiDays = apis.exploredDays;

    $('#loading').hidden = true;
    initTabs();
    initFeedControls();
    initLookup();
    initRequestFormat();
    renderFeed();
    renderCadence();
    renderFooter(events.generatedAt);
    showTab('feed');
  } catch (err) {
    $('#loading').hidden = true;
    const box = $('#error');
    box.hidden = false;
    box.textContent = `Could not load the index: ${err.message}. Run the indexer, then reload.`;
  }
}

function renderFooter(generatedAt) {
  const cov = state.meta.coverage;
  $('#footer-meta').textContent =
    `${cov.snapshots} snapshots · ${cov.firstSnapshot} to ${cov.lastSnapshot} · ` +
    `${cov.catalogSize.toLocaleString()} permissions in catalog · index built ${generatedAt}`;
}

/* ---------------- tabs ---------------- */

function initTabs() {
  document.querySelectorAll('.tab').forEach((tab) => {
    tab.addEventListener('click', () => showTab(tab.id.replace('tab-', '')));
  });
}

function showTab(name) {
  ['feed', 'lookup', 'cadence'].forEach((n) => {
    const isActive = n === name;
    $(`#tab-${n}`).setAttribute('aria-selected', String(isActive));
    $(`#panel-${n}`).hidden = !isActive;
  });
  if (name === 'lookup') {
    $('#lookup-input').focus();
    ensureLookupLoaded();
  }
}

/* ---------------- feed ---------------- */

function initFeedControls() {
  document.querySelectorAll('.chip[data-tier]').forEach((chip) => {
    chip.addEventListener('click', () => {
      const tier = Number(chip.dataset.tier);
      if (state.tiers.has(tier)) state.tiers.delete(tier);
      else state.tiers.add(tier);
      chip.classList.toggle('on', state.tiers.has(tier));
      chip.setAttribute('aria-pressed', String(state.tiers.has(tier)));
      renderFeed();
    });
  });

  document.querySelectorAll('.range').forEach((btn) => {
    btn.addEventListener('click', () => {
      state.days = Number(btn.dataset.days);
      document.querySelectorAll('.range').forEach((b) =>
        b.setAttribute('aria-pressed', String(b === btn)));
      renderFeed();
    });
  });

  let debounce;
  $('#feed-filter').addEventListener('input', (e) => {
    clearTimeout(debounce);
    debounce = setTimeout(() => {
      state.filter = e.target.value.trim().toLowerCase();
      renderFeed();
    }, 120);
  });

  $('#show-removed').addEventListener('change', (e) => {
    state.showRemoved = e.target.checked;
    renderFeed();
  });
}

function cutoffDate() {
  if (!state.days) return null;
  const d = new Date();
  d.setDate(d.getDate() - state.days);
  return d.toISOString().slice(0, 10);
}

function visibleEvents() {
  const cutoff = cutoffDate();
  return state.events.filter((e) => {
    if (cutoff && e.date < cutoff) return false;
    if (e.change === 'removed' && !state.showRemoved) return false;
    if (!state.tiers.has(e.tier)) return false;
    if (state.filter) {
      const hay = `${e.title} ${e.service} ${e.permissions.join(' ')}`.toLowerCase();
      if (!hay.includes(state.filter)) return false;
    }
    return true;
  });
}

function renderFeed() {
  const cutoff = cutoffDate();
  const inRange = state.events.filter(
    (e) => (!cutoff || e.date >= cutoff) && (e.change === 'added' || state.showRemoved)
  );

  // Counts on the chips describe the range, not the current tier selection, so
  // turning a tier off does not make its own count vanish.
  [0, 1, 2].forEach((tier) => {
    const n = inRange.filter((e) => e.tier === tier).length;
    document.querySelector(`.chip-count[data-count="${tier}"]`).textContent = n;
  });

  renderFeedStats(inRange);

  const events = visibleEvents();
  const container = $('#feed');
  container.textContent = '';
  $('#feed-empty').hidden = events.length > 0;

  const permCount = events.reduce((sum, e) => sum + e.count, 0);
  $('#feed-summary').textContent = events.length
    ? `${events.length} event${events.length === 1 ? '' : 's'} covering ${permCount} permission${permCount === 1 ? '' : 's'}.`
    : '';

  const byDate = new Map();
  for (const event of events) {
    if (!byDate.has(event.date)) byDate.set(event.date, []);
    byDate.get(event.date).push(event);
  }

  const fragment = document.createDocumentFragment();
  for (const [date, group] of byDate) {
    const section = el('section', 'daygroup');
    const heading = el('h3');
    heading.appendChild(el('span', null, date));
    const total = group.reduce((sum, e) => sum + e.count, 0);
    heading.appendChild(el('span', 'weekday',
      `${WEEKDAY[parseDate(date).getDay()]} · ${total} permission${total === 1 ? '' : 's'}`));
    section.appendChild(heading);
    group.forEach((event) => section.appendChild(renderCard(event)));
    fragment.appendChild(section);
  }
  container.appendChild(fragment);
}

function renderFeedStats(inRange) {
  const box = $('#feed-stats');
  box.textContent = '';
  const services = inRange.filter((e) => e.tier === 0 && e.change === 'added');
  const stats = [
    { value: inRange.length, label: 'events', cls: '' },
    { value: services.length, label: 'new services', cls: 't0' },
    { value: inRange.filter((e) => e.tier === 1).length, label: 'new resources', cls: 't1' },
    { value: inRange.reduce((s, e) => s + e.count, 0), label: 'permissions', cls: '' },
  ];
  for (const s of stats) {
    const cell = el('div', `stat ${s.cls}`);
    cell.appendChild(el('div', 'value', s.value.toLocaleString()));
    cell.appendChild(el('div', 'label', s.label));
    box.appendChild(cell);
  }
}

function renderCard(event) {
  const card = el('details', `card t${event.tier}${event.change === 'removed' ? ' removed' : ''}`);
  if (event.tier === 0) card.open = true;

  const summary = el('summary');
  summary.appendChild(el('span', 'chevron', '›'));

  const badge = el('span',
    `badge ${event.change === 'removed' ? 'rm' : 't' + event.tier}`,
    event.change === 'removed' ? `removed ${event.tierLabel}` : event.tierLabel);
  summary.appendChild(badge);

  summary.appendChild(el('span', 'title', event.title));
  const api = apiBadge(event.service);
  if (api) summary.appendChild(api);
  summary.appendChild(el('span', 'count',
    `${event.count} permission${event.count === 1 ? '' : 's'}`));
  card.appendChild(summary);

  const body = el('div', 'card-body');

  body.appendChild(el('h4', null, event.change === 'removed' ? 'Permissions removed' : 'Permissions'));
  const list = el('ul', 'perms');
  for (const permission of event.permissions) {
    const item = el('li');
    item.dataset.perm = permission;
    const idx = permission.lastIndexOf('.');
    item.appendChild(document.createTextNode(permission.slice(0, idx + 1)));
    item.appendChild(el('span', 'verb', permission.slice(idx + 1)));
    list.appendChild(item);
  }
  body.appendChild(list);

  if (event.roles && event.roles.length) {
    body.appendChild(el('h4', null, 'Granted through broad roles'));
    const roles = el('div', 'roles');
    for (const role of event.roles) {
      roles.appendChild(el('span', 'role broad', role.name));
    }
    body.appendChild(roles);
  }

  const serviceRoles = (state.services && state.services[event.service]) || [];
  if (serviceRoles.length) {
    body.appendChild(el('h4', null, `Roles for ${event.service}`));
    const roles = el('div', 'roles');
    for (const role of serviceRoles.slice(0, 14)) {
      const chip = el('span', 'role');
      chip.appendChild(document.createTextNode(role.name));
      if (role.stage) chip.appendChild(el('span', 'stage', ` ${role.stage}`));
      roles.appendChild(chip);
    }
    if (serviceRoles.length > 14) {
      roles.appendChild(el('span', 'role', `+${serviceRoles.length - 14} more`));
    }
    body.appendChild(roles);
  }

  if (event.change === 'added') body.appendChild(el('div', 'api-section'));
  card.appendChild(body);

  let enhanced = false;
  const enhance = () => {
    if (enhanced || !card.open) return;
    enhanced = true;
    loadDetail(event.service).then((detail) => enhanceCard(card, event, detail));
  };
  card.addEventListener('toggle', enhance);
  if (card.open) queueMicrotask(enhance);
  return card;
}

/* ---------------- lookup ---------------- */

let lookupPromise = null;

function ensureLookupLoaded() {
  if (lookupPromise) return lookupPromise;
  const status = $('#lookup-status');
  status.hidden = false;
  status.textContent = 'Loading permission index…';
  lookupPromise = loadJSON('lookup.json')
    .then((data) => {
      state.lookup = data;
      status.hidden = true;
      runLookup();
      return data;
    })
    .catch((err) => {
      status.textContent = `Could not load the permission index: ${err.message}`;
    });
  return lookupPromise;
}

function initLookup() {
  let debounce;
  $('#lookup-input').addEventListener('input', () => {
    clearTimeout(debounce);
    debounce = setTimeout(runLookup, 140);
  });
}

function runLookup() {
  const box = $('#lookup-results');
  box.textContent = '';
  if (!state.lookup) return;

  const raw = $('#lookup-input').value.trim();
  if (!raw) return;
  const query = raw.toLowerCase();

  const permissions = state.lookup.permissions;

  // Permission names are camelCase (compute.instances.osLogin), so an exact hit
  // has to be found case-insensitively rather than by keying on the lowercased
  // query.
  let exactName = Object.prototype.hasOwnProperty.call(permissions, raw) ? raw : null;
  const matches = [];
  for (const name in permissions) {
    const lower = name.toLowerCase();
    if (!exactName && lower === query) {
      exactName = name;
      continue;
    }
    if (name !== exactName && lower.includes(query)) {
      matches.push([name, permissions[name]]);
      if (matches.length > 400) break;
    }
  }

  if (exactName) {
    box.appendChild(renderAnswer(exactName, permissions[exactName]));
  }

  if (!exactName && !matches.length) {
    box.appendChild(el('p', 'empty', `Nothing in the catalog matches “${query}”.`));
    return;
  }

  if (matches.length) {
    matches.sort((a, b) => (a[1][0] === b[1][0] ? a[0].localeCompare(b[0]) : b[1][0].localeCompare(a[1][0])));
    const wrap = el('div', 'table-wrap');
    const table = el('table', 'results');
    const head = el('tr');
    const hasStage = Boolean(state.meta.metadata && state.meta.metadata.available);
    ['Permission', 'First seen', 'Status'].concat(hasStage ? ['Stage'] : [])
      .forEach((h) => head.appendChild(el('th', null, h)));
    table.appendChild(head);
    for (const [name, rec] of matches) {
      const row = el('tr');
      row.appendChild(el('td', null, name));
      row.appendChild(el('td', 'date', rec[1] ? 'predates record' : rec[0]));
      row.appendChild(el('td', rec[2] ? 'date gone' : 'date', rec[2] ? `removed ${rec[2]}` : 'active'));
      if (hasStage) row.appendChild(el('td', 'date', rec[3] || ''));
      table.appendChild(row);
    }
    wrap.appendChild(table);
    box.appendChild(el('p', 'summary', `${matches.length}${matches.length > 400 ? '+' : ''} other matches`));
    box.appendChild(wrap);
  }
}

function renderAnswer(name, rec) {
  const [first, predates, removed] = rec;
  const card = el('div', 'answer');
  card.appendChild(el('div', 'perm', name));

  if (predates) {
    card.appendChild(el('div', 'when unknown', `Already existed on ${state.lookup.baseline}`));
    card.appendChild(el('div', 'detail', 'This permission predates the record, so it cannot be dated more precisely.'));
  } else {
    const date = parseDate(first);
    card.appendChild(el('div', 'when', first));
    card.appendChild(el('div', 'detail',
      `First observed on a ${WEEKDAY[date.getDay()]}${removed ? '' : ', still in the catalog'}.`));
  }

  if (removed) {
    card.appendChild(el('div', 'detail', `Removed from the catalog on ${removed}.`));
  }

  const gap = gapCovering(first);
  if (gap && !predates) {
    card.appendChild(el('div', 'caveat',
      `Collection gap: nothing was recorded between ${gap.from} and ${gap.to} (${gap.days} days), ` +
      `so this may have appeared any time in that window.`));
  }

  const sameDay = state.events.filter((e) => e.date === first && e.change === 'added');
  if (sameDay.length) {
    const total = sameDay.reduce((s, e) => s + e.count, 0);
    card.appendChild(el('div', 'detail',
      `${total} permission${total === 1 ? '' : 's'} landed that day across ${sameDay.length} event${sameDay.length === 1 ? '' : 's'}.`));
  }

  const extra = el('div', 'answer-extra');
  card.appendChild(extra);
  loadDetail(name.split('.')[0]).then((detail) => {
    if (extra.isConnected) renderAnswerDetail(extra, name, detail);
  });
  return card;
}

function gapCovering(date) {
  const gaps = state.meta.coverage.gaps || [];
  return gaps.find((g) => date > g.from && date <= g.to);
}

/* ---------------- cadence ---------------- */

function renderCadence() {
  const cadence = state.cadence;
  const coverage = state.meta.coverage;

  const headline = $('#cadence-headline');
  headline.textContent = '';
  if (cadence.totalAdded) {
    headline.appendChild(document.createTextNode('Most new permissions appear on '));
    headline.appendChild(el('strong', null, cadence.peakDay));
    headline.appendChild(document.createTextNode(
      ` — ${cadence.peakShare}% of the ${cadence.totalAdded.toLocaleString()} permissions added since ${cadence.reliableFrom}.`));
  } else {
    headline.textContent = 'Not enough dense history yet to judge a release rhythm.';
  }

  $('#cadence-caveat').textContent =
    `Counted only from ${cadence.reliableFrom}, where snapshots are dense enough to attribute a day. ` +
    (cadence.collectionHourUtc != null
      ? `The collector runs at ${String(cadence.collectionHourUtc).padStart(2, '0')}:00 UTC, so each bar covers the 24 hours ending then.`
      : '');

  const max = Math.max(...cadence.weekdays.map((w) => w.added), 1);
  const chart = $('#weekday-chart');
  chart.textContent = '';
  for (const day of cadence.weekdays) {
    chart.appendChild(bar(day.day, day.added, max, `${day.share}%`,
      `${day.snapshots} snapshot${day.snapshots === 1 ? '' : 's'}`,
      day.day === cadence.peakDay));
  }

  const monthly = cadence.monthly.slice(-18);
  const maxMonth = Math.max(...monthly.map((m) => m.added), 1);
  const monthChart = $('#monthly-chart');
  monthChart.textContent = '';
  for (const month of monthly) {
    monthChart.appendChild(bar(
      month.month, month.added, maxMonth, String(month.added),
      month.reliable ? '' : 'incomplete', false));
  }

  const cov = $('#coverage');
  cov.textContent = '';
  const lines = [
    ['Snapshots recorded', String(coverage.snapshots)],
    ['Record begins', coverage.firstSnapshot],
    ['Most recent snapshot', coverage.lastSnapshot],
    ['Dense enough to date precisely since', coverage.reliableFrom || '—'],
    ['Collection gaps longer than ' + coverage.reliableGapDays + ' days', String((coverage.gaps || []).length)],
    ['Corrupt snapshots excluded', String((coverage.anomalies || []).length)],
    ['Permission metadata', state.meta.metadata && state.meta.metadata.available
      ? `${state.meta.metadata.permissions.toLocaleString()} permissions`
      : 'not collected yet'],
    ['API docs explored', state.meta.discovery && state.meta.discovery.days != null
      ? `${state.meta.discovery.explored} services · ` + Object.entries(state.meta.discovery.byStatus)
        .map(([status, n]) => `${status.replace('_', ' ')} ${n}`).join(', ')
      : 'off'],
  ];
  for (const [label, value] of lines) {
    const row = el('div', 'cov-line');
    row.appendChild(el('span', null, label));
    row.appendChild(el('span', null, value));
    cov.appendChild(row);
  }
  for (const gap of coverage.gaps || []) {
    const row = el('div', 'cov-line');
    row.appendChild(el('span', 'gap-warn', `Gap: ${gap.from} → ${gap.to}`));
    row.appendChild(el('span', 'gap-warn', `${gap.days} days`));
    cov.appendChild(row);
  }
  for (const bad of coverage.anomalies || []) {
    const row = el('div', 'cov-line');
    row.appendChild(el('span', 'gap-warn', `Corrupt snapshot ${bad.sha} on ${bad.date}`));
    row.appendChild(el('span', 'gap-warn', `${bad.previous} → ${bad.observed}`));
    cov.appendChild(row);
  }
}

function bar(name, value, max, valueLabel, note, isPeak) {
  const row = el('div', `bar-row${isPeak ? ' peak' : ''}`);
  row.appendChild(el('div', 'name', name));
  const track = el('div', 'bar-track');
  const fill = el('div', 'bar-fill');
  fill.style.width = `${Math.max((value / max) * 100, value ? 2 : 0)}%`;
  track.appendChild(fill);
  row.appendChild(track);
  const label = el('div', 'value');
  label.appendChild(el('span', 'n', valueLabel));
  if (note) label.appendChild(document.createTextNode(` · ${note}`));
  row.appendChild(label);
  return row;
}

/* ---------------- permission metadata & API explorer ---------------- */

const FORMAT_KEY = 'observatory.requestFormat';

const API_STATUS = {
  listed: { label: 'API public', cls: 'ok', title: 'Discovery document is public and listed in Google’s API directory' },
  unlisted: { label: 'API unlisted', cls: 'unlisted', title: 'Discovery document is served publicly but the API is not in Google’s directory' },
  restricted: { label: 'API needs key', cls: 'restricted', title: 'The API host exists but will not serve its Discovery document to an anonymous caller' },
  not_found: { label: 'no API host', cls: 'none', title: 'Nothing answers at this service’s googleapis.com host for the versions tried' },
};

function initRequestFormat() {
  try {
    const format = localStorage.getItem(FORMAT_KEY);
    if (format === 'curl' || format === 'http') state.requestFormat = format;
  } catch {
    // Storage unavailable (private window, blocked site data): the choice just doesn't persist.
  }
}

function apiBadge(service) {
  const info = state.apis[service];
  const spec = info && API_STATUS[info.status];
  if (!spec) return null;
  const label = info.status === 'restricted' && info.reasonKind !== 'identity' ? 'API restricted' : spec.label;
  const badge = el('span', `api-badge ${spec.cls}`, label);
  badge.title = spec.title;
  return badge;
}

function loadDetail(service) {
  if (!/^[a-z][a-z0-9-]*$/.test(service)) return Promise.resolve(null);
  if (!state.details.has(service)) {
    state.details.set(service,
      fetch(`detail/${service}.json`, { cache: 'no-cache' })
        .then((res) => (res.ok ? res.json() : null))
        .catch(() => null));
  }
  return state.details.get(service);
}

function enhanceCard(card, event, detail) {
  const permissions = (detail && detail.permissions) || {};
  card.querySelectorAll('ul.perms li[data-perm]').forEach((entry) => {
    const meta = permissions[entry.dataset.perm];
    if (!meta) return;
    if (meta.stage && meta.stage !== 'GA') {
      entry.appendChild(el('span', `stage-badge ${meta.stage.toLowerCase()}`, meta.stage));
    }
    if (meta.title) entry.appendChild(el('div', 'perm-title', meta.title));
  });

  const section = card.querySelector('.api-section');
  if (section) renderApiSection(section, event, detail);
}

function renderApiSection(section, event, detail) {
  section.textContent = '';
  section.appendChild(el('h4', null, 'API explorer'));

  const info = detail && detail.discovery;
  if (!info) {
    let missing = 'API exploration is turned off for this build.';
    if (state.apiDays === 0) missing = 'No API document was fetched: this service is no longer in the permission catalog.';
    else if (state.apiDays) missing = `Not explored. API documents are fetched for services with additions in the last ${state.apiDays} days.`;
    section.appendChild(el('p', 'api-note', missing));
    return;
  }

  section.appendChild(renderDiscoveryStatus(info, event.service));
  const methods = detail.methods || [];
  if (!methods.length) return;

  let shown = methods;
  let note = `${methods.length} method${methods.length === 1 ? '' : 's'}.`;
  if (event.tier !== 0) {
    const matching = methods.filter((m) => m.resource === event.resource);
    if (matching.length) {
      shown = matching;
      note = `${matching.length} of ${methods.length} methods act on ${event.resource}.`;
    } else {
      note = `No method could be matched to ${event.resource}; showing all ${methods.length} for ${event.service}.`;
    }
  }
  section.appendChild(el('p', 'api-note', note));

  const list = el('div', 'methods');
  renderMethodBatch(list, shown, detail, 0);
  section.appendChild(list);
}

const METHOD_BATCH = 40;

function renderMethodBatch(list, methods, detail, start) {
  const end = Math.min(start + METHOD_BATCH, methods.length);
  for (let i = start; i < end; i++) list.appendChild(renderMethod(methods[i], detail));
  if (end < methods.length) {
    const more = el('button', 'more', `Show ${methods.length - end} more`);
    more.addEventListener('click', () => {
      more.remove();
      renderMethodBatch(list, methods, detail, end);
    });
    list.appendChild(more);
  }
}

function renderDiscoveryStatus(info, service) {
  const box = el('div', `api-status ${info.status}`);
  const line = el('div', 'api-status-line');
  const badge = apiBadge(service);
  if (badge) line.appendChild(badge);
  if (info.title) line.appendChild(el('span', 'api-title', info.title));
  if (info.version) line.appendChild(el('span', 'api-version', info.version));
  if (info.documentationLink && /^https:\/\//.test(info.documentationLink)) {
    const link = el('a', 'api-doc', 'docs ↗');
    link.href = info.documentationLink;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    line.appendChild(link);
  }
  box.appendChild(line);

  const explanation = {
    listed: 'Published in Google’s public API directory.',
    unlisted: 'The Discovery document is served publicly, but the API is not listed in Google’s directory.',
    restricted: `${service}.googleapis.com answered, but refused an anonymous request for its Discovery document: “${info.reason || 'permission denied'}” This explorer only makes anonymous requests, so the endpoints stay hidden here.`,
    not_found: `Nothing answered at ${service}.googleapis.com. The permissions may guard an API published under a different name.`,
    error: 'Google could not be reached for this service during the last build; it is retried on the next one.',
  }[info.status];
  if (explanation) box.appendChild(el('p', 'api-note', explanation));

  const tried = (info.tried || []).map((t) => `${t.version}→${t.code == null ? 'no response' : t.code}`).join('  ');
  if (tried) box.appendChild(el('p', 'api-tried', `tried: ${tried}`));
  return box;
}

function renderMethod(method, detail, focus) {
  const row = el('details', 'method');
  const summary = el('summary');
  summary.appendChild(el('span', `http ${method.httpMethod.toLowerCase()}`, method.httpMethod));
  summary.appendChild(el('code', 'method-path', method.path));
  if (method.permissions.length) {
    const shown = focus && method.permissions.includes(focus) ? focus : method.permissions[0];
    const extraCount = method.permissions.length - 1;
    summary.appendChild(el('span', 'method-perm', shown + (extraCount ? ` +${extraCount}` : '')));
  }
  row.appendChild(summary);
  row.addEventListener('toggle', () => {
    if (row.open && row.childElementCount === 1) row.appendChild(renderMethodBody(method, detail));
  });
  return row;
}

function renderMethodBody(method, detail) {
  const body = el('div', 'method-body');
  body.appendChild(el('div', 'method-id', method.id));
  if (method.description) body.appendChild(el('p', 'method-desc', method.description));

  body.appendChild(el('h5', null, 'Required permission'));
  if (method.permissions.length) {
    const perms = el('div', 'roles');
    method.permissions.forEach((p) => perms.appendChild(el('span', 'role', p)));
    perms.appendChild(el('span', 'perm-source',
      method.permissionSource === 'mapped' ? 'from iam-dataset method map' : 'inferred from the method name'));
    body.appendChild(perms);
  } else {
    body.appendChild(el('p', 'api-note',
      'Unknown — not in the method map, and no permission in the catalog matches the method name.'));
  }

  if (method.parameters.length) {
    body.appendChild(el('h5', null, 'Parameters'));
    const table = el('table', 'params');
    const head = el('tr');
    ['Name', 'In', 'Type', 'Description'].forEach((h) => head.appendChild(el('th', null, h)));
    table.appendChild(head);
    for (const param of method.parameters) {
      const tr = el('tr');
      tr.appendChild(el('td', 'mono', param.name + (param.required ? ' *' : '')));
      tr.appendChild(el('td', null, param.location));
      tr.appendChild(el('td', 'mono', param.type + (param.enum ? ` (${param.enum.join(' | ')})` : '')));
      tr.appendChild(el('td', 'desc', param.description || ''));
      table.appendChild(tr);
    }
    const wrap = el('div', 'table-wrap');
    wrap.appendChild(table);
    body.appendChild(wrap);
  }

  const schemas = detail.schemas || {};
  if (method.request) {
    body.appendChild(el('h5', null, 'Request body'));
    body.appendChild(renderSchema(method.request, schemas, 0));
  }
  if (method.response) {
    body.appendChild(el('h5', null, 'Response'));
    body.appendChild(renderSchema(method.response, schemas, 0));
  }

  const tryHead = el('div', 'try-head');
  tryHead.appendChild(el('h5', null, 'Try it'));
  const toggle = el('div', 'format-toggle');
  toggle.setAttribute('role', 'group');
  toggle.setAttribute('aria-label', 'Request format');
  const buttons = {};
  for (const [format, label] of [['curl', 'curl'], ['http', 'raw HTTP']]) {
    const button = el('button', 'format', label);
    button.type = 'button';
    button.addEventListener('click', () => setRequestFormat(format));
    buttons[format] = button;
    toggle.appendChild(button);
  }
  tryHead.appendChild(toggle);
  body.appendChild(tryHead);

  const wrap = el('div', 'curl-wrap');
  const pre = el('pre', 'curl');
  const code = el('code');
  pre.appendChild(code);
  const copy = el('button', 'copy', 'Copy');
  copy.addEventListener('click', () => copyText(code.textContent, copy));
  wrap.appendChild(pre);
  wrap.appendChild(copy);
  body.appendChild(wrap);

  const hint = el('p', 'hint');
  body.appendChild(hint);

  const entry = { code, hint, buttons, method, discovery: detail.discovery, schemas };
  state.tryIts.push(entry);
  renderTryIt(entry);
  return body;
}

function renderSchema(name, schemas, depth) {
  const box = el('div', 'schema');
  const schema = schemas[name];
  box.appendChild(el('div', 'schema-name', name));
  if (!schema) {
    box.appendChild(el('p', 'api-note', 'Definition not included in this build.'));
    return box;
  }
  if (schema.description && depth === 0) box.appendChild(el('p', 'schema-desc', schema.description));

  const props = Object.entries(schema.properties || {});
  const list = el('ul', 'schema-props');
  if (!props.length) list.appendChild(el('li', 'api-note', schema.type || 'object'));
  for (const [prop, spec] of props) list.appendChild(renderProperty(prop, spec, schemas, depth));
  box.appendChild(list);
  return box;
}

function renderProperty(prop, spec, schemas, depth) {
  const entry = el('li');
  const ref = spec.$ref || (spec.items && spec.items.$ref);
  let typeLabel;
  if (spec.$ref) typeLabel = spec.$ref;
  else if (spec.type === 'array') typeLabel = `${(spec.items && (spec.items.$ref || spec.items.type)) || 'any'}[]`;
  else typeLabel = (spec.type || 'any') + (spec.format ? ` (${spec.format})` : '');

  const line = el('div', 'prop-line');
  line.appendChild(el('span', 'prop-name', prop));
  line.appendChild(el('span', 'prop-type', typeLabel));
  if (spec.readOnly) line.appendChild(el('span', 'prop-flag', 'output only'));
  if (spec.enum) line.appendChild(el('span', 'prop-enum', spec.enum.join(' | ')));
  const desc = spec.description ? el('div', 'prop-desc', spec.description) : null;

  if (ref && schemas[ref] && depth < 6) {
    const nested = el('details', 'prop-nested');
    const summary = el('summary');
    summary.appendChild(line);
    nested.appendChild(summary);
    if (desc) nested.appendChild(desc);
    nested.addEventListener('toggle', () => {
      if (nested.open && !nested.querySelector(':scope > .schema')) {
        nested.appendChild(renderSchema(ref, schemas, depth + 1));
      }
    });
    entry.appendChild(nested);
  } else {
    entry.appendChild(line);
    if (desc) entry.appendChild(desc);
  }
  return entry;
}

function placeholderName(name) {
  return name
    .replace(/([a-z0-9])([A-Z])/g, '$1_$2')
    .replace(/[^A-Za-z0-9_]/g, '_')
    .toUpperCase();
}

function pathValue(name) {
  if (/^projects?(Id)?$/.test(name)) return 'PROJECT_ID';
  if (/^locations?(Id)?$/.test(name)) return 'LOCATION';
  return placeholderName(name);
}

function buildRequest(method, discovery, schemas) {
  const path = method.path.replace(/\{\+?([^}]+)\}/g, (_, name) => pathValue(name));
  let url = `${discovery.rootUrl || ''}${discovery.servicePath || ''}${path}`;
  const requiredQuery = method.parameters.filter((p) => p.location === 'query' && p.required);
  if (requiredQuery.length) {
    url += '?' + requiredQuery.map((p) => `${p.name}=${pathValue(p.name)}`).join('&');
  }

  const headers = [];
  let body = null;
  if (method.request) {
    body = JSON.stringify(bodySkeleton(method.request, schemas, 0), null, 2);
    headers.push(['Content-Type', 'application/json']);
  }
  return { httpMethod: method.httpMethod, url, headers, body, tokenCommand: 'gcloud auth print-access-token' };
}

function formatCurl(req) {
  const lines = [`curl -X ${req.httpMethod} \\`, `  -H "Authorization: Bearer $(${req.tokenCommand})" \\`];
  for (const [name, value] of req.headers) lines.push(`  -H "${name}: ${value}" \\`);
  if (req.body != null) lines.push(`  -d '${req.body.replace(/\n/g, '\n  ')}' \\`);
  lines.push(`  "${req.url}"`);
  return lines.join('\n');
}

function formatRawHttp(req) {
  const target = new URL(req.url);
  const lines = [
    `${req.httpMethod} ${target.pathname}${target.search} HTTP/1.1`,
    `Host: ${target.host}`,
    'Authorization: Bearer ACCESS_TOKEN',
    ...req.headers.map(([name, value]) => `${name}: ${value}`),
  ];
  if (req.body != null) lines.push(`Content-Length: ${new TextEncoder().encode(req.body).length}`);
  // HTTP/1.1 separates header lines with CRLF. Tools that replay a pasted raw
  // request byte for byte (openssl s_client, some proxies) need it exact.
  return `${lines.join('\r\n')}\r\n\r\n${req.body ?? ''}`;
}

function renderTryIt(entry) {
  const req = buildRequest(entry.method, entry.discovery, entry.schemas);
  const raw = state.requestFormat === 'http';
  entry.code.textContent = raw ? formatRawHttp(req) : formatCurl(req);
  entry.hint.textContent = raw
    ? `Replace ACCESS_TOKEN with the output of \`${req.tokenCommand}\`, and the UPPER_CASE placeholders with your values.`
    : 'Replace the UPPER_CASE placeholders with your values. ' +
      'The command asks gcloud for a short-lived token; nothing on this page holds credentials.';
  for (const [format, button] of Object.entries(entry.buttons)) {
    button.setAttribute('aria-pressed', String(format === state.requestFormat));
  }
}

function bodySkeleton(ref, schemas, depth) {
  const schema = schemas[ref];
  if (!schema || depth > 2) return {};
  const out = {};
  for (const [prop, spec] of Object.entries(schema.properties || {})) {
    if (spec.readOnly) continue;
    if (spec.$ref) out[prop] = bodySkeleton(spec.$ref, schemas, depth + 1);
    else if (spec.type === 'array') out[prop] = [];
    else if (spec.type === 'boolean') out[prop] = false;
    else if (spec.type === 'integer' || spec.type === 'number') out[prop] = 0;
    else if (spec.type === 'object') out[prop] = {};
    else out[prop] = '';
  }
  return out;
}

function refreshRequests() {
  state.tryIts = state.tryIts.filter((entry) => entry.code.isConnected);
  state.tryIts.forEach(renderTryIt);
}

function setRequestFormat(format) {
  state.requestFormat = format;
  try {
    localStorage.setItem(FORMAT_KEY, format);
  } catch {
    // Storage unavailable: the choice lasts for this page view only.
  }
  refreshRequests();
}

function copyText(text, button) {
  const done = (label) => {
    button.textContent = label;
    setTimeout(() => { button.textContent = 'Copy'; }, 1500);
  };
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(() => done('Copied'), () => done('Copy failed'));
  } else {
    done('Select to copy');
  }
}

function renderAnswerDetail(box, name, detail) {
  const meta = detail && detail.permissions && detail.permissions[name];
  if (meta) {
    if (meta.title) box.appendChild(el('div', 'answer-title', meta.title));
    if (meta.description) box.appendChild(el('div', 'detail', meta.description));
    const facts = el('div', 'facts');
    if (meta.stage) facts.appendChild(el('span', `stage-badge ${meta.stage.toLowerCase()}`, meta.stage));
    if (meta.customRolesSupportLevel) {
      facts.appendChild(el('span', 'fact',
        `custom roles: ${meta.customRolesSupportLevel.toLowerCase().replace('_', ' ')}`));
    }
    if (meta.primaryPermission) facts.appendChild(el('span', 'fact', `primary permission: ${meta.primaryPermission}`));
    if (facts.childElementCount) box.appendChild(facts);
  }

  // A method whose primary permission this is (instances.insert for
  // compute.instances.create) matters more than one that merely also needs it.
  const methods = ((detail && detail.methods) || [])
    .filter((m) => m.permissions.includes(name))
    .sort((a, b) => Number(b.permissions[0] === name) - Number(a.permissions[0] === name));
  if (methods.length) {
    box.appendChild(el('h5', null, `Required by ${methods.length} API method${methods.length === 1 ? '' : 's'}`));
    const list = el('div', 'methods');
    methods.forEach((m) => list.appendChild(renderMethod(m, detail, name)));
    box.appendChild(list);
  } else if (detail && detail.discovery) {
    const service = detail.service;
    const text = {
      listed: 'No method in the published API could be matched to this permission.',
      unlisted: 'No method in the unlisted API could be matched to this permission.',
      restricted: `${service}.googleapis.com will not serve its Discovery document anonymously, so its methods are not visible.`,
      not_found: `Nothing answers at ${service}.googleapis.com.`,
      error: 'The API document could not be fetched during the last build.',
    }[detail.discovery.status];
    if (text) box.appendChild(el('p', 'api-note', text));
  }
}

boot();
