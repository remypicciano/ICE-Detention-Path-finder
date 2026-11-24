(() => {
  'use strict';

  const body = document.body;
  const base = body.dataset.base || '';
  const HISTORY_KEY = 'bronx.history.v1';
  const HISTORY_LIMIT = 5000;
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
        window.location.href = `${base}/?q=${encodeURIComponent(entry.identifier)}`;
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

  function setupMainPage() {
    const resultText = $('#result-text');
    const identifier = $('#identifier');

    if (resultText && resultText.value) {
      const entry = JSON.parse(body.dataset.historyEntry || 'null');
      if (entry?.identifier !== 'UFAKE-0001') addHistory(entry);
    }
    renderRecent();

    // A query string from the history page pre-fills the identifier box.
    const preset = new URLSearchParams(window.location.search).get('q');
    if (preset && identifier) identifier.value = preset;

    $('#copy-button')?.addEventListener('click', async () => {
      if (!resultText || !resultText.value) return;
      const status = $('#status-line');
      try {
        await navigator.clipboard.writeText(resultText.value);
        if (status) status.textContent = 'Timeline copied to clipboard.';
      } catch (_error) {
        resultText.select();
        document.execCommand('copy');
        if (status) status.textContent = 'Timeline copied to clipboard.';
      }
    });

    $('#export-button')?.addEventListener('click', () => {
      if (!resultText || !resultText.value || identifier?.value === 'UFAKE-0001') return;
      setHidden('#export-identifier', identifier?.value || '');
      setHidden('#export-release', $('#release')?.value || '');
      $('#export-form')?.submit();
    });

    $('#update-button')?.addEventListener('click', () => {
      setHidden('#update-identifier', identifier?.value || '');
      setHidden('#update-release', $('#release')?.value || '');
      setHidden('#update-overrides', $('#overrides-input')?.value || '{}');
      $('#update-form')?.submit();
    });

    const dialog = $('#manual-dialog');
    const manualButton = $('#manual-button');
    const editable = JSON.parse(body.dataset.editableFields || '[]');
    const stayCount = Number(body.dataset.stayCount || 0);
    const overrides = JSON.parse(body.dataset.overrides || '{}');

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
      setHidden('#manual-release', $('#release')?.value || '');
      setHidden('#manual-overrides', $('#overrides-input')?.value || '{}');
      dialog?.showModal();
    });
    $('#manual-cancel')?.addEventListener('click', () => dialog?.close());
    $('#manual-clear')?.addEventListener('click', () => {
      $$('#manual-fields input[data-key]').forEach((input) => { input.value = ''; });
    });
    $('#manual-form')?.addEventListener('submit', () => {
      const values = {};
      $$('#manual-fields input[data-key]').forEach((input) => {
        const value = input.value.trim();
        if (value) values[input.dataset.key] = value;
      });
      setHidden('#manual-overrides', JSON.stringify(values));
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

  setupMainPage();
  setupBulkPage();
  setupHistoryPage();
})();
