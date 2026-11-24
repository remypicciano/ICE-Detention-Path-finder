(() => {
  'use strict';

  const body = document.body;
  const base = body.dataset.base || '';
  const HISTORY_KEY = 'bronx.history.v1';
  const HISTORY_LIMIT = 5000;
  const LOOKUP_CACHE_KEY = 'bronx.lookup-cache.v1';
  const LOOKUP_CACHE_LIMIT = 60;
  const lookupMemory = new Map();
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  function readHistory() {
    try {
      const value = JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]');
      return Array.isArray(value) ? value : [];
    } catch (_error) {
      return [];
    }
  }

  function writeHistory(entries) {
    try {
      localStorage.setItem(HISTORY_KEY, JSON.stringify(entries.slice(-HISTORY_LIMIT)));
    } catch (_error) {
      // Private browsing or a full quota must not break a lookup.
    }
  }

  function readLookupCache(key) {
    if (lookupMemory.has(key)) return lookupMemory.get(key);
    try {
      const stored = JSON.parse(sessionStorage.getItem(LOOKUP_CACHE_KEY) || '{}');
      const payload = stored[key];
      if (payload) {
        lookupMemory.set(key, payload);
        return payload;
      }
    } catch (_error) {
      // A cache miss just costs one lookup.
    }
    return null;
  }

  function writeLookupCache(key, payload) {
    lookupMemory.set(key, payload);
    if (lookupMemory.size > LOOKUP_CACHE_LIMIT) {
      const oldest = lookupMemory.keys().next().value;
      lookupMemory.delete(oldest);
    }
    try {
      const stored = JSON.parse(sessionStorage.getItem(LOOKUP_CACHE_KEY) || '{}');
      stored[key] = payload;
      const keys = Object.keys(stored);
      if (keys.length > LOOKUP_CACHE_LIMIT) {
        keys.slice(0, keys.length - LOOKUP_CACHE_LIMIT).forEach((old) => delete stored[old]);
      }
      sessionStorage.setItem(LOOKUP_CACHE_KEY, JSON.stringify(stored));
    } catch (_error) {
      // Memory cache still works when storage is unavailable or full.
    }
  }

  function addHistory(entry) {
    if (!entry || !entry.identifier) return;
    const entries = readHistory().filter((item) => item.identifier !== entry.identifier);
    entries.push(entry);
    writeHistory(entries);
  }

  function formatWhen(value) {
    if (!value) return '';
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
  }

  function fillRow(row, entry) {
    const cells = [
      formatWhen(entry.searched_at),
      entry.identifier || '',
      String(entry.n_stays ?? ''),
      String(entry.n_rows ?? ''),
      entry.label || '',
    ];
    cells.forEach((text, index) => {
      const cell = document.createElement('td');
      cell.textContent = text;
      row.appendChild(cell);
    });
  }

  function openIdentifier(identifier) {
    const input = $('#identifier');
    if (!input) return;
    input.value = identifier;
    $('#search-form')?.requestSubmit();
  }

  function renderRecent() {
    const tbody = $('#recent-table tbody');
    if (!tbody) return;
    tbody.replaceChildren();
    const entries = readHistory().slice(-10).reverse();
    if (!entries.length) {
      const row = document.createElement('tr');
      const cell = document.createElement('td');
      cell.colSpan = 4;
      cell.textContent = 'No lookups in this browser yet.';
      row.appendChild(cell);
      tbody.appendChild(row);
      return;
    }
    entries.forEach((entry) => {
      const row = document.createElement('tr');
      row.className = 'clickable';
      fillRow(row, entry);
      row.addEventListener('click', () => openIdentifier(entry.identifier));
      tbody.appendChild(row);
    });
  }

  function renderHistory() {
    const tbody = $('#history-table tbody');
    if (!tbody) return;
    const filter = ($('#history-filter')?.value || '').trim().toLowerCase();
    const entries = readHistory().slice().reverse().filter((entry) => {
      if (!filter) return true;
      return `${entry.identifier} ${entry.label || ''}`.toLowerCase().includes(filter);
    });
    tbody.replaceChildren();
    entries.forEach((entry) => {
      const row = document.createElement('tr');
      row.className = 'clickable';
      fillRow(row, entry);
      row.addEventListener('click', () => {
        const params = new URLSearchParams({ q: entry.identifier });
        if (body.dataset.style && body.dataset.style !== 'record') {
          params.set('style', body.dataset.style);
        }
        window.location.href = `${base}/?${params}`;
      });
      tbody.appendChild(row);
    });
    const empty = $('#history-empty');
    if (empty) empty.hidden = entries.length > 0;
  }

  function setHidden(id, value) {
    const field = $(id);
    if (field) field.value = value;
  }

  function syncStyleForm(identifier, release) {
    const form = $('.style-switch');
    if (!form) return;
    const q = $('[name="q"]', form);
    const releaseField = $('[name="release"]', form);
    if (q) q.value = identifier;
    if (releaseField) releaseField.value = release;
  }

  function syncTopbarHeight() {
    const topbar = $('.topbar');
    if (!topbar) return;
    const height = Math.ceil(topbar.getBoundingClientRect().height);
    if (height > 0) document.documentElement.style.setProperty('--topbar-h', `${height}px`);
  }

  async function copyTimeline(append) {
    const resultText = $('#result-text');
    if (!resultText || !resultText.value) return;
    const status = $('#status-line');
    try {
      await navigator.clipboard.writeText(resultText.value);
    } catch (_error) {
      resultText.select();
      document.execCommand('copy');
    }
    if (!status) return;
    const message = 'Timeline copied to clipboard.';
    if (!append) status.textContent = message;
    else if (!status.textContent.includes(message)) {
      status.textContent = `${status.textContent} ${message}`;
    }
  }

  function applyStyle(style) {
    body.dataset.style = style;
    $('meta[name="color-scheme"]')?.setAttribute('content', style === 'console' ? 'dark' : 'light');
    $$('input[type="hidden"][name="style"]').forEach((field) => {
      field.value = style;
    });
    syncStyleForm($('#identifier')?.value || '', $('#release')?.value || '');
    $$('a[href]').forEach((link) => {
      const href = link.getAttribute('href') || '';
      if (!href.startsWith('/') || href.startsWith('//') || href.includes('/export.')) return;
      const url = new URL(href, window.location.origin);
      if (url.origin !== window.location.origin) return;
      if (style === 'record') url.searchParams.delete('style');
      else url.searchParams.set('style', style);
      link.setAttribute('href', `${url.pathname}${url.search}${url.hash}`);
    });
  }

  function setupStyleSwitch() {
    const select = $('#style-select');
    applyStyle(body.dataset.style || 'record');
    select?.addEventListener('change', () => {
      const style = select.value;
      applyStyle(style);
      const url = new URL(window.location.href);
      if (style === 'record') url.searchParams.delete('style');
      else url.searchParams.set('style', style);
      window.history.replaceState(null, '', url);
    });
  }

  function setupMainPage() {
    const resultText = $('#result-text');
    const identifier = $('#identifier');
    const statusLine = $('#status-line');
    const dialog = $('#manual-dialog');
    const manualButton = $('#manual-button');
    const editable = JSON.parse(body.dataset.editableFields || '[]');
    const inline = body.dataset.inlineSearch === '1';
    const cacheVersion = body.dataset.indexBuild || '0';
    let stayCount = Number(body.dataset.stayCount || 0);
    let overrides = JSON.parse(body.dataset.overrides || '{}');
    let pending = false;

    const currentRelease = () => $('#release')?.value || '';
    const cacheKey = (entered, release, overrideText) =>
      [cacheVersion, release, entered, overrideText].join('|');

    // A query string from the history page pre-fills the identifier box.
    const preset = new URLSearchParams(window.location.search).get('q');
    if (preset && identifier) identifier.value = preset;

    if (resultText && resultText.value) {
      const entry = JSON.parse(body.dataset.historyEntry || 'null');
      if (entry?.identifier && entry.identifier !== 'UFAKE-0001') {
        addHistory(entry);
        void copyTimeline(true);
      }
      if (inline && identifier) {
        const overrideText = $('#overrides-input')?.value || '{}';
        writeLookupCache(cacheKey(identifier.value, currentRelease(), overrideText), {
          ok: true,
          identifier: entry?.identifier || identifier.value,
          release: currentRelease(),
          text: resultText.value,
          status: statusLine?.textContent || '',
          stays: stayCount,
          rows: entry?.n_rows || 0,
          overrides,
          historyEntry: entry,
        });
      }
    }
    renderRecent();

    $('#copy-button')?.addEventListener('click', () => void copyTimeline(false));
    $('#copy-inline-button')?.addEventListener('click', () => void copyTimeline(false));

    $('#export-button')?.addEventListener('click', () => {
      if (!resultText || !resultText.value || identifier?.value === 'UFAKE-0001') return;
      setHidden('#export-identifier', identifier?.value || '');
      setHidden('#export-release', currentRelease());
      $('#export-form')?.submit();
    });

    $('#update-button')?.addEventListener('click', () => {
      setHidden('#update-identifier', identifier?.value || '');
      setHidden('#update-release', currentRelease());
      setHidden('#update-overrides', $('#overrides-input')?.value || '{}');
      $('#update-form')?.submit();
    });

    function applyLookup(payload, entered, release) {
      if (!resultText || !identifier) return;
      const overrideText = JSON.stringify(payload.overrides || {});
      identifier.value = entered;
      syncStyleForm(entered, release);
      resultText.value = payload.text || '';
      if (statusLine) statusLine.textContent = payload.status || '';
      stayCount = Number(payload.stays || 0);
      overrides = payload.overrides || {};
      body.dataset.stayCount = String(stayCount);
      body.dataset.overrides = overrideText;
      if (payload.historyEntry) body.dataset.historyEntry = JSON.stringify(payload.historyEntry);
      else delete body.dataset.historyEntry;
      setHidden('#overrides-input', overrideText);
      setHidden('#manual-identifier', entered);
      setHidden('#manual-release', release);
      setHidden('#manual-overrides', overrideText);
      setHidden('#export-identifier', entered);
      setHidden('#export-release', release);
      setHidden('#update-identifier', entered);
      setHidden('#update-release', release);
      setHidden('#update-overrides', overrideText);
      if (payload.historyEntry) addHistory(payload.historyEntry);
      renderRecent();
      if (manualButton) manualButton.disabled = !resultText.value || entered === 'UFAKE-0001';
      const url = new URL(window.location.href);
      url.searchParams.set('q', entered);
      url.searchParams.set('release', release);
      window.history.replaceState(null, '', url);
      if (resultText.value) void copyTimeline(true);
    }

    function applyLookupError(message) {
      if (resultText) resultText.value = '';
      if (statusLine) statusLine.textContent = message;
      syncStyleForm(identifier?.value || '', currentRelease());
      body.dataset.stayCount = '0';
      body.dataset.overrides = '{}';
      delete body.dataset.historyEntry;
      setHidden('#overrides-input', '{}');
      overrides = {};
      stayCount = 0;
      if (manualButton) manualButton.disabled = true;
      renderRecent();
    }

    async function submitLookup(form, entered, release, overrideText) {
      if (!entered) {
        if (statusLine) statusLine.textContent = 'Enter an identifier first.';
        return;
      }
      if (pending) return;
      const key = cacheKey(entered, release, overrideText);
      const cached = readLookupCache(key);
      if (cached) {
        applyLookup(cached, entered, release);
        return;
      }
      pending = true;
      if (statusLine) statusLine.textContent = 'Searching…';
      try {
        const response = await fetch('/api/lookup', {
          method: 'POST',
          body: new FormData(form),
          headers: { accept: 'application/json' },
          credentials: 'same-origin',
        });
        const payload = await response.json().catch(() => ({
          error: `Search failed: HTTP ${response.status}`,
        }));
        if (!response.ok || payload.error) {
          applyLookupError(payload.error || `Search failed: HTTP ${response.status}`);
          return;
        }
        writeLookupCache(key, payload);
        applyLookup(payload, entered, release);
      } catch (error) {
        applyLookupError(`Search failed: ${error.message}`);
      } finally {
        pending = false;
      }
    }

    $('#search-form')?.addEventListener('submit', (event) => {
      if (!inline) return;
      event.preventDefault();
      void submitLookup(
        event.currentTarget,
        identifier?.value || '',
        currentRelease(),
        $('#overrides-input')?.value || '{}',
      );
    });

    if (manualButton && (!resultText || !resultText.value || identifier?.value === 'UFAKE-0001')) {
      manualButton.disabled = true;
    }

    function buildManualFields() {
      const host = $('#manual-fields');
      if (!host) return;
      host.replaceChildren();
      const scopes = [{ label: 'All stays', prefix: '' }];
      for (let number = 1; number <= stayCount; number += 1) {
        scopes.push({ label: `STAY ${number} of ${stayCount}`, prefix: `stay-${number}/` });
      }
      scopes.forEach((scope) => {
        const group = document.createElement('section');
        group.className = 'manual-group';
        const heading = document.createElement('h3');
        heading.textContent = scope.label;
        group.appendChild(heading);
        const grid = document.createElement('div');
        grid.className = 'manual-grid';
        editable.forEach((field) => {
          const key = scope.prefix + field;
          const id = `manual-${key.replace(/[^A-Za-z0-9]+/g, '-')}`;
          const label = document.createElement('label');
          label.htmlFor = id;
          label.textContent = field;
          const input = document.createElement('input');
          input.type = 'text';
          input.id = id;
          input.dataset.key = key;
          input.maxLength = 200;
          input.value = overrides[key] || '';
          grid.append(label, input);
        });
        group.appendChild(grid);
        host.appendChild(group);
      });
    }

    manualButton?.addEventListener('click', () => {
      buildManualFields();
      setHidden('#manual-identifier', identifier?.value || '');
      setHidden('#manual-release', currentRelease());
      setHidden('#manual-overrides', $('#overrides-input')?.value || '{}');
      dialog?.showModal();
    });
    $('#manual-cancel')?.addEventListener('click', () => dialog?.close());
    $('#manual-clear')?.addEventListener('click', () => {
      $$('#manual-fields input[data-key]').forEach((input) => { input.value = ''; });
    });
    $('#manual-form')?.addEventListener('submit', (event) => {
      const values = {};
      $$('#manual-fields input[data-key]').forEach((input) => {
        const value = input.value.trim();
        if (value) values[input.dataset.key] = value;
      });
      const overrideText = JSON.stringify(values);
      setHidden('#manual-overrides', overrideText);
      if (!inline) return;
      event.preventDefault();
      dialog?.close();
      void submitLookup(event.currentTarget, identifier?.value || '', currentRelease(), overrideText);
    });
  }

  function setupBulkPage() {
    const data = $('#bulk-results-data');
    if (!data) return;
    let results = [];
    try { results = JSON.parse(data.dataset.results || '[]'); } catch (_error) { results = []; }
    results.forEach((result) => addHistory(result.history_entry));
    const detail = $('#bulk-detail');
    $$('#bulk-table tr[data-detail-index]').forEach((row) => {
      row.addEventListener('click', () => {
        const result = results[Number(row.dataset.detailIndex)];
        if (!result) return;
        detail.value = result.status === 'ok'
          ? (result.text || '(no pathway)')
          : `ERROR: ${result.error || result.detail || result.status}`;
      });
    });
  }

  function setupHistoryPage() {
    renderHistory();
    $('#history-filter')?.addEventListener('input', renderHistory);
  }

  syncTopbarHeight();
  window.addEventListener('resize', syncTopbarHeight);
  setupMainPage();
  setupBulkPage();
  setupHistoryPage();
  setupStyleSwitch();
})();
