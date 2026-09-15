'use strict';

const state = {
  events: [],
  meta: null,
  cadence: null,
  lookup: null,          // loaded on demand; it is the largest file
  services: {},
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
    const [events, meta, cadence, services] = await Promise.all([
      loadJSON('events.json'),
      loadJSON('meta.json'),
      loadJSON('cadence.json'),
      loadJSON('services.json').catch(() => ({ rolesByService: {} })),
    ]);
    state.events = events.events;
    state.meta = meta;
    state.cadence = cadence;
    state.services = services.rolesByService || {};

    $('#loading').hidden = true;
    initTabs();
    initFeedControls();
    initLookup();
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
  summary.appendChild(el('span', 'count',
    `${event.count} permission${event.count === 1 ? '' : 's'}`));
  card.appendChild(summary);

  const body = el('div', 'card-body');

  body.appendChild(el('h4', null, event.change === 'removed' ? 'Permissions removed' : 'Permissions'));
  const list = el('ul', 'perms');
  for (const permission of event.permissions) {
    const item = el('li');
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

  card.appendChild(body);
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
    ['Permission', 'First seen', 'Status'].forEach((h) => head.appendChild(el('th', null, h)));
    table.appendChild(head);
    for (const [name, rec] of matches) {
      const row = el('tr');
      row.appendChild(el('td', null, name));
      row.appendChild(el('td', 'date', rec[1] ? 'predates record' : rec[0]));
      row.appendChild(el('td', rec[2] ? 'date gone' : 'date', rec[2] ? `removed ${rec[2]}` : 'active'));
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

boot();
