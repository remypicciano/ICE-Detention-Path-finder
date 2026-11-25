// Server-rendered HTML. No data access or request routing here.

import {
  editedDisplayValue,
  WEB_EDITABLE_FIELDS,
  type Overrides,
  type PathwayDoc,
} from "./format";

export type ReleaseInfo = {
  key: string;
  label: string;
  tag: string;
  watermark: string;
  identifiers: number;
  shards: number;
};

export type Manifest = {
  schema_version: number;
  built_at: string;
  app_version: string;
  default_release: string;
  releases: ReleaseInfo[];
};

export const STYLES = ["record", "manifest", "reading", "film"] as const;
export type Style = (typeof STYLES)[number];

const STYLE_ALIASES: Record<string, Style> = {
  console: "manifest",
  dossier: "reading",
};

export function styleOf(value: unknown): Style {
  const key = typeof value === "string" ? value.trim().toLowerCase() : "";
  if (STYLES.includes(key as Style)) return key as Style;
  return STYLE_ALIASES[key] ?? "record";
}

export type Result = {
  doc: PathwayDoc;
  overrides: Overrides;
  text: string;
  status: string;
  identifier: string;
  historyEntry: {
    identifier: string;
    searched_at: string;
    n_stays: number;
    n_rows: number;
    label: string | null;
  };
};

export type BulkResult = {
  identifier: string;
  label: string | null;
  status: "ok" | "error";
  pathway: PathwayDoc | null;
  text: string | null;
  detail: string;
  error?: string;
  history_entry: Result["historyEntry"] | null;
};

export type IndexOptions = {
  manifest: Manifest;
  result: Result | null;
  status: string;
  error: string | null;
  updateMessage: string | null;
  enteredIdentifier: string;
  enteredRelease: string;
  style: Style;
};

export function escapeHtml(value: unknown): string {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

const attr = escapeHtml;

function isoNow(): string {
  return new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
}

function releaseOptions(releases: ReleaseInfo[], selected: string): string {
  if (!releases.length) return '<option value="">No datasets installed</option>';
  return releases
    .map(
      (r) =>
        `<option value="${attr(r.key)}"${r.key === selected ? " selected" : ""}>${attr(r.label)}</option>`,
    )
    .join("");
}

const STYLE_LABELS: Record<Style, string> = {
  record: "Record",
  manifest: "Manifest",
  reading: "Reading room",
  film: "Microfilm",
};

function styleSuffix(style: Style): string {
  return style === "record" ? "" : `?style=${style}`;
}

function styleSwitch(style: Style, identifier: string, release: string, action = "/"): string {
  const options = STYLES.map(
    (key) =>
      `<option value="${key}"${key === style ? " selected" : ""}>${STYLE_LABELS[key]}</option>`,
  ).join("");
  return `<form class="style-switch" method="get" action="${attr(action)}">
  <label for="style-select">Staging style</label>
  <select id="style-select" name="style">${options}</select>
  <input type="hidden" name="q" value="${attr(identifier)}">
  <input type="hidden" name="release" value="${attr(release)}">
</form>`;
}

function overlayForms(overrides: Overrides, style: Style, view = "search"): string {
  return `<dialog id="manual-dialog" class="modal">
  <form id="manual-form" method="post" action="/manual" class="stack">
    <h2>Manual entries</h2>
    <p class="hint">Leave a box empty to publish the recorded value. A filled box replaces it and is tagged <code>[MANUAL_EDIT original=…]</code> where it lands. “All stays” applies everywhere a stay does not override it.</p>
    <input type="hidden" name="identifier" id="manual-identifier">
    <input type="hidden" name="release" id="manual-release">
    <input type="hidden" name="overrides" id="manual-overrides">
    <input type="hidden" name="view" value="${attr(view)}">
    <input type="hidden" name="style" value="${attr(style)}">
    <div id="manual-fields" class="manual-fields"></div>
    <div class="modal-actions">
      <button type="button" id="manual-clear" class="secondary">Clear all</button>
      <button type="button" id="manual-cancel" class="secondary">Cancel</button>
      <button type="submit" class="primary">Apply</button>
    </div>
  </form>
</dialog>

<form id="export-form" method="post" action="/export-json" class="hidden-form">
  <input type="hidden" name="identifier" id="export-identifier">
  <input type="hidden" name="release" id="export-release">
</form>

<form id="update-form" method="post" action="/update-check" class="hidden-form">
  <input type="hidden" name="identifier" id="update-identifier">
  <input type="hidden" name="release" id="update-release">
  <input type="hidden" name="overrides" id="update-overrides">
  <input type="hidden" name="view" value="${attr(view)}">
  <input type="hidden" name="style" value="${attr(style)}">
</form>`;
}

function notices(options: IndexOptions): string {
  return [
    options.updateMessage
      ? `<div class="notice" role="status"><pre>${escapeHtml(options.updateMessage)}</pre></div>`
      : "",
    options.error ? `<div class="alert" role="alert">${escapeHtml(options.error)}</div>` : "",
  ].join("\n");
}

function enteredIdentifier(options: IndexOptions): string {
  const result = options.result;
  return options.enteredIdentifier || (result && result.identifier !== "UFAKE-0001" ? result.identifier : "");
}

export function layout(
  title: string,
  content: string,
  manifest: Manifest,
  result: Result | null = null,
  inlineSearch = false,
  style: Style = "record",
  staging = false,
): string {
  const editable = JSON.stringify(WEB_EDITABLE_FIELDS);
  const nav = [
    ["Search", "/"],
    ...(staging ? [["Table", "/table"]] : []),
    ["Bulk search", "/bulk"],
    ["History", "/history"],
    ["Datasets", "/datasets"],
    ["Help", "/help"],
  ]
    .map(
      ([label, href]) =>
        `<a href="${href}${styleSuffix(style)}"${label === title ? ' aria-current="page"' : ""}>${label}</a>`,
    )
    .join("");
  const resultAttrs = result
    ? `data-stay-count="${result.doc.totals.stays}" data-history-entry='${attr(JSON.stringify(result.historyEntry))}' data-overrides='${attr(JSON.stringify(result.overrides))}'`
    : 'data-stay-count="0" data-overrides=\'{}\'';
  return `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="${style === "record" ? "light" : "dark"}">
  <title>${attr(title)} · Bronx</title>
  <link rel="icon" href="/favicon.ico" sizes="any">
  <link rel="stylesheet" href="/style.css">
  <script src="/app.js" defer></script>
</head>
<body data-base="" ${inlineSearch ? 'data-inline-search="1"' : ""} data-style="${attr(style)}" data-index-build="${attr(manifest.built_at)}" data-editable-fields='${attr(editable)}' ${resultAttrs}>
  <header class="topbar">
    <a class="brand" href="/${styleSuffix(style)}">
      <span class="brand-mark" aria-hidden="true">❄</span>
      <span>ICE Detention Pathway</span>
      <span class="version">v${attr(manifest.app_version)}</span>
    </a>
    <nav aria-label="Main">
      ${nav}
    </nav>
  </header>

  <main class="shell">
${content}
  </main>

  <footer class="footer">
    <p>Independent tool. Government data provided by ICE in response to a FOIA request, processed by the Deportation Data Project, and analyzed with ICE Detention Pathway.</p>
    <p>Pathways are recorded history, not confirmed outcomes. Missing release dates do not mean a person is still detained. Verify consequential claims against primary records.</p>
  </footer>
</body>
</html>`;
}

// Production keeps the two-column workbench.
export function indexPage(options: IndexOptions): string {
  const { manifest, result, status } = options;
  const selected = options.enteredRelease || manifest.default_release;
  const overrides = result ? result.overrides : {};
  const content = `<section class="hero">
  <h1>Reconstruct a recorded route through ICE custody.</h1>
  <p class="tagline">“Get that fish a lawyer”</p>
  <p class="lede">Paste a <code>unique_identifier</code>, <code>stay_ID</code>, or <code>stint_ID</code>. Every recorded stay is shown separately, in book-in order.</p>
</section>

${notices(options)}

<div class="workbench">
  <aside class="query-rail">
    <div class="rail-block">
      <form id="search-form" method="post" action="/search" class="stack">
        <input type="hidden" name="overrides" id="overrides-input" value='${attr(JSON.stringify(overrides))}'>
        <input type="hidden" name="view" value="search">

        <div class="field-row">
          <div class="field grow">
            <label for="identifier">Unique identifier</label>
            <input id="identifier" name="identifier" type="text" maxlength="200" autocomplete="off" spellcheck="false"
                   placeholder="Paste an identifier…" value="${attr(enteredIdentifier(options))}">
          </div>
          <div class="field">
            <label for="release">Release</label>
            <select id="release" name="release">${releaseOptions(manifest.releases, selected)}</select>
          </div>
          <div class="field actions-inline">
            <button type="submit" class="primary">Search</button>
          </div>
        </div>
        <p class="hint">Press Return in the identifier box to search. <a href="/demo">Load the demo pathway</a>.</p>
      </form>

      <div class="toolbar">
        <button type="button" id="manual-button" class="secondary">All manual entries…</button>
        <button type="button" id="copy-button" class="secondary">Copy to Clipboard</button>
        <button type="button" id="export-button" class="secondary">Export JSON…</button>
        <button type="button" id="update-button" class="secondary">Check for Updates…</button>
        <a class="secondary" href="/bulk">Bulk Search…</a>
      </div>
    </div>

    <div class="rail-block">
      <p class="status" id="status-line" role="status">${escapeHtml(status)}</p>
    </div>

    <div class="rail-block recent">
      <div class="section-head">
        <h2>Recent lookups</h2>
        <a href="/history">View all…</a>
      </div>
      <table class="data-table" id="recent-table">
        <thead><tr><th>Searched</th><th>Identifier</th><th>Stays</th><th>Rows</th></tr></thead>
        <tbody></tbody>
      </table>
      <p class="hint">Click a row to open that lookup. History stays in this browser.</p>
    </div>
  </aside>

  <section class="result-sheet">
    <div class="field">
      <label for="result-text">Detention timeline (editable display)</label>
      <textarea id="result-text" spellcheck="false" rows="14" aria-label="Detention timeline">${escapeHtml(result?.text ?? "")}</textarea>
    </div>
  </section>
</div>

${overlayForms(overrides, "record")}`;
  return layout("Search", content, manifest, result);
}

// Staging keeps one lookup workflow, with three distinct visual directions.
export function lookupPage(options: IndexOptions): string {
  const { manifest, result, status, style } = options;
  const selected = options.enteredRelease || manifest.default_release;
  const entered = enteredIdentifier(options);
  const overrides = result ? result.overrides : {};
  const head = `<div class="panel-head">
    <div class="panel-title">
      <h1>Reconstruct a recorded route through ICE custody.</h1>
      <p class="lede">Paste a <code>unique_identifier</code>, <code>stay_ID</code>, or <code>stint_ID</code>. Every recorded stay is shown separately, in book-in order.</p>
    </div>
    ${styleSwitch(style, entered, selected)}
  </div>`;
  const noticeSlot = `<div class="notice-slot">${notices(options)}</div>`;
  const form = `<form id="search-form" method="post" action="/search" class="lookup-form">
    <input type="hidden" name="overrides" id="overrides-input" value='${attr(JSON.stringify(overrides))}'>
    <input type="hidden" name="view" value="search">
    <input type="hidden" name="style" value="${attr(style)}">
    <label class="visually-hidden" for="identifier">Unique identifier</label>
    <input id="identifier" name="identifier" type="text" maxlength="200" autocomplete="off" spellcheck="false"
           placeholder="Paste an identifier…" value="${attr(entered)}">
    <button type="submit" class="primary">Search</button>
  </form>`;
  const dataset = `<details class="dataset-picker">
    <summary>Dataset</summary>
    <div class="field">
      <label for="release">Release</label>
      <select id="release" name="release" form="search-form">${releaseOptions(manifest.releases, selected)}</select>
    </div>
  </details>`;
  const actions = `<div class="lookup-actions">
    <button type="button" id="copy-button" class="secondary">Copy to Clipboard</button>
    <button type="button" id="manual-button" class="secondary">All manual entries…</button>
    <button type="button" id="export-button" class="secondary">Export JSON…</button>
    <button type="button" id="update-button" class="secondary">Check for Updates…</button>
    <a href="/demo${styleSuffix(style)}">Load the demo pathway</a>
    <a href="/bulk${styleSuffix(style)}">Bulk Search…</a>
  </div>`;
  const output = `<div class="output-block">
    <div class="output-head">
      <h2>Pathway</h2>
      <button type="button" id="copy-inline-button" class="secondary">Copy</button>
    </div>
    <p class="status" id="status-line" role="status">${escapeHtml(status)}</p>
    <textarea id="result-text" spellcheck="false" rows="14" aria-label="Detention timeline">${escapeHtml(result?.text ?? "")}</textarea>
  </div>`;
  const content = `<section class="single-panel staging-layout">
  ${head}
  ${noticeSlot}
  <div class="intake">
    <p class="intake-label">Search</p>
    ${form}
    ${dataset}
  </div>
  ${output}
  ${actions}
</section>

${overlayForms(overrides, style)}`;
  return layout("Search", content, manifest, result, true, style, true);
}

type TableArrest = {
  date_time: string | null;
  date_only: string | null;
  location: string | null;
  landmark: string | null;
  apprehension_state_fill_source: string | null;
  _landmark_field?: string;
};

type TableRow = { book_in: string | null; facility: string | null; book_out: string | null };

type TableStay = {
  stay_id: string | null;
  arrest: TableArrest | null;
  entry_program: string | null;
  entry_aor: string | null;
  release_reason: string | null;
  stint_ids: (string | null)[];
  program_variance: string | null;
  aor_variance: string | null;
  start: string | null;
  end: string | null;
  summary: Record<string, string | null> | null;
  rows: TableRow[];
  _all_programs?: string[];
  _all_aors?: string[];
  _summary_fields?: Record<string, string | null> | null;
};

const TABLE_SUMMARY_FIELDS = [
  ["classification", "classification"],
  ["case_status", "case_status"],
  ["threat_level", "threat_level"],
  ["final_order", "final_order"],
  ["final_order_date", "final_order_date"],
  ["departed", "departed"],
  ["charge", "final_charge"],
] as const;

function editedCell(value: string): string {
  const raw = value || "";
  const match = raw.match(/^(.*?) \[MANUAL_EDIT original=(.*)\]$/);
  if (!match) return raw ? escapeHtml(raw) : '<span class="muted">—</span>';
  const shown = match[1] ? escapeHtml(match[1]) : '<span class="muted">—</span>';
  return `${shown} <span class="manual-tag">[MANUAL_EDIT original=${escapeHtml(match[2])}]</span>`;
}

function utcCell(value: string | null): string {
  if (!value) return '<span class="unknown">UNKNOWN</span>';
  const hasZone = value.endsWith("Z") || /[+-]\d{2}:\d{2}$/.test(value);
  const when = Date.parse(hasZone ? value : `${value}Z`);
  if (Number.isNaN(when)) return escapeHtml(value);
  const shown = new Date(when).toISOString().slice(0, 19).replace("T", " ");
  return `<time datetime="${attr(value)}">${shown} UTC</time>`;
}

function fact(label: string, value: string): string {
  return `<div><dt>${escapeHtml(label)}</dt><dd>${value}</dd></div>`;
}

function stayFacts(stay: TableStay, number: number, overrides: Overrides): string {
  const arrest = stay.arrest;
  const arrestDate = arrest
    ? arrest.date_time
      ? utcCell(arrest.date_time)
      : arrest.date_only
        ? escapeHtml(arrest.date_only.slice(0, 10))
        : "UNKNOWN ARREST DATE"
    : escapeHtml("NO ARREST RECORD IN THIS DATASET");
  const location = arrest
    ? editedCell(
        editedDisplayValue(overrides, "location", arrest.location || "UNKNOWN ARREST LOCATION", number),
      )
    : '<span class="muted">—</span>';
  const landmark = arrest
    ? editedCell(editedDisplayValue(overrides, "landmark", arrest.landmark, number))
    : '<span class="muted">—</span>';
  const provenance = arrest
    ? editedCell(arrest.apprehension_state_fill_source || "")
    : '<span class="muted">—</span>';
  const values = stay._summary_fields ?? stay.summary ?? {};
  const summary = TABLE_SUMMARY_FIELDS.filter(([, key]) => Boolean(values[key])).map(([label, key]) =>
    fact(label, editedCell(editedDisplayValue(overrides, label, values[key], number))),
  );
  const programs = (stay._all_programs || []).filter((value) => value && value !== stay.entry_program);
  const aors = (stay._all_aors || []).filter((value) => value && value !== stay.entry_aor);
  return `<dl class="stay-facts">
  ${fact("arrest", arrestDate)}
  ${fact("location", location)}
  ${fact("landmark", landmark)}
  ${fact("provenance", provenance)}
  ${fact("final_program", editedCell(editedDisplayValue(overrides, "final_program", stay.entry_program, number)))}
  ${fact("book_in_aor", editedCell(editedDisplayValue(overrides, "book_in_aor", stay.entry_aor, number)))}
  ${fact("release_reason", editedCell(stay.release_reason || ""))}
  ${programs.length ? fact("stint programs", editedCell(programs.join("; "))) : ""}
  ${aors.length ? fact("stint AORs", editedCell(aors.join("; "))) : ""}
  ${summary.join("\n  ")}
</dl>`;
}

function stintRows(stay: TableStay): string {
  if (!stay.rows.length) {
    return '<tr><td colspan="5">NO DETENTION RECORD IN THIS DATASET</td></tr>';
  }
  return stay.rows
    .map(
      (row, index) => {
        const stintId = stay.stint_ids[index];
        return `<tr>
        <td>${index + 1}</td>
        <td>${stintId ? `<code>${escapeHtml(stintId)}</code>` : '<span class="muted">—</span>'}</td>
        <td>${utcCell(row.book_in)}</td>
        <td>${escapeHtml(row.facility || "UNKNOWN DETENTION CENTER")}</td>
        <td>${utcCell(row.book_out)}</td>
      </tr>`;
      },
    )
    .join("\n");
}

function staysTable(doc: PathwayDoc, overrides: Overrides): string {
  const stays = doc.stays as TableStay[];
  const focused = doc.focus_stay_id;
  const sections = stays.map((stay, index) => {
    const number = index + 1;
    const isFocused = focused !== null && stay.stay_id === focused;
    return `<section class="stay-block${isFocused ? " focused" : ""}">
  <div class="stay-head">
    <h2>STAY ${number} of ${stays.length}</h2>
    <code>${escapeHtml(stay.stay_id || "UNKNOWN STAY ID")}</code>
    ${isFocused ? '<span class="focus-mark">FOCUS</span>' : ""}
  </div>
  ${stayFacts(stay, number, overrides)}
  <div class="data-table-wrap">
    <table class="data-table stays-table">
      <thead><tr><th>#</th><th>Stint ID</th><th>Book in (UTC)</th><th>Facility</th><th>Book out (UTC)</th></tr></thead>
      <tbody>${stintRows(stay)}</tbody>
    </table>
  </div>
</section>`;
  });
  return sections.length
    ? sections.join("\n")
    : '<p class="table-empty">NO DETENTION RECORD IN THIS DATASET</p>';
}

function looseArrestsTable(doc: PathwayDoc, overrides: Overrides): string {
  const arrests = doc.arrests_without_stay as TableArrest[];
  if (!arrests.length) return "";
  const rows = arrests
    .map((arrest, index) => {
      const date = arrest.date_time
        ? utcCell(arrest.date_time)
        : arrest.date_only
          ? escapeHtml(arrest.date_only.slice(0, 10))
          : "UNKNOWN ARREST DATE";
      return `<tr>
        <td>${index + 1}</td>
        <td>${date}</td>
        <td>${editedCell(editedDisplayValue(overrides, "location", arrest.location || "UNKNOWN ARREST LOCATION"))}</td>
        <td>${editedCell(editedDisplayValue(overrides, "landmark", arrest.landmark))}</td>
        <td>${editedCell(arrest.apprehension_state_fill_source || "")}</td>
      </tr>`;
    })
    .join("\n");
  return `<section class="stay-block loose-arrests">
  <div class="stay-head"><h2>Arrests without a stay</h2></div>
  <div class="data-table-wrap">
    <table class="data-table stays-table">
      <thead><tr><th>#</th><th>Arrest date</th><th>Location</th><th>Landmark</th><th>Provenance</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  </div>
</section>`;
}

export function tablePage(options: IndexOptions): string {
  const { manifest, result, status, style } = options;
  const entered = enteredIdentifier(options);
  const selected = options.enteredRelease || manifest.default_release;
  const overrides = result ? result.overrides : {};
  const head = `<div class="panel-head">
    <div class="panel-title">
      <h1>Reconstruct a recorded route through ICE custody.</h1>
      <p class="lede">Paste a <code>unique_identifier</code>, <code>stay_ID</code>, or <code>stint_ID</code>. Every recorded stay is shown separately, in book-in order.</p>
    </div>
    ${styleSwitch(style, entered, selected, "/table")}
  </div>`;
  const form = `<form id="search-form" method="get" action="/table" class="lookup-form">
    <input type="hidden" name="overrides" id="overrides-input" value='${attr(JSON.stringify(overrides))}'>
    <input type="hidden" name="view" value="table">
    <input type="hidden" name="style" value="${attr(style)}">
    <label class="visually-hidden" for="identifier">Unique identifier</label>
    <input id="identifier" name="q" type="text" maxlength="200" autocomplete="off" spellcheck="false"
           placeholder="Paste an identifier…" value="${attr(entered)}">
    <button type="submit" class="primary">Search</button>
  </form>`;
  const dataset = `<details class="dataset-picker">
    <summary>Dataset</summary>
    <div class="field">
      <label for="release">Release</label>
      <select id="release" name="release" form="search-form">${releaseOptions(manifest.releases, selected)}</select>
    </div>
  </details>`;
  const actions = `<div class="lookup-actions">
    <button type="button" id="copy-button" class="secondary">Copy to Clipboard</button>
    <button type="button" id="manual-button" class="secondary">All manual entries…</button>
    <button type="button" id="export-button" class="secondary">Export JSON…</button>
    <button type="button" id="update-button" class="secondary">Check for Updates…</button>
    <a href="/demo${styleSuffix(style)}">Load the demo pathway</a>
    <a href="/bulk${styleSuffix(style)}">Bulk Search…</a>
  </div>`;
  const table = result
    ? `${staysTable(result.doc, overrides)}${looseArrestsTable(result.doc, overrides)}`
    : '<p class="table-empty">Paste an identifier and select Search.</p>';
  const content = `<section class="single-panel table-panel staging-layout">
  ${head}
  <div class="notice-slot">${notices(options)}</div>
  <div class="intake">
    <p class="intake-label">Search</p>
    ${form}
    ${dataset}
  </div>
  <div class="table-block">
    <p class="status" id="status-line" role="status">${escapeHtml(status)}</p>
    <textarea id="result-text" hidden spellcheck="false">${escapeHtml(result?.text ?? "")}</textarea>
    ${table}
  </div>
  ${actions}
</section>

${overlayForms(overrides, style, "table")}`;
  return layout("Table", content, manifest, result, false, style, true);
}

export function bulkPage(
  manifest: Manifest,
  results: BulkResult[] | null,
  summary: { found: number; failed: number; total: number } | null,
  error: string | null,
  token: string | null,
  style: Style = "record",
  staging = false,
): string {
  const alert = error ? `<div class="alert" role="alert">${escapeHtml(error)}</div>` : "";
  let resultPanel = "";
  if (results && summary && token) {
    const rows = results
      .map(
        (r, i) => `<tr class="clickable" data-detail-index="${i}">
        <td><span class="pill ${r.status === "ok" ? "ok" : "bad"}">${attr(r.status)}</span></td>
        <td>${escapeHtml(r.identifier)}</td>
        <td>${escapeHtml(r.label ?? "")}</td>
        <td>${escapeHtml(r.detail)}</td>
      </tr>`,
      )
      .join("\n");
    resultPanel = `
<section class="panel">
  <div class="section-head">
    <h2>Results</h2>
    <div class="toolbar inline">
      <a class="secondary" href="/bulk/${attr(token)}/export.json">Export JSON…</a>
      <a class="secondary" href="/bulk/${attr(token)}/export.txt">Export Text…</a>
    </div>
  </div>
  <p class="status">Done: ${summary.found} found, ${summary.failed} failed of ${summary.total}.</p>
  <table class="data-table" id="bulk-table">
    <thead><tr><th>Status</th><th>Identifier</th><th>Label</th><th>Details</th></tr></thead>
    <tbody>${rows}</tbody>
  </table>
  <div class="field detail-field">
    <label for="bulk-detail">Detail</label>
    <textarea id="bulk-detail" rows="12" readonly></textarea>
  </div>
  <div id="bulk-results-data" data-results='${attr(JSON.stringify(results))}' hidden></div>
</section>`;
  }
  const content = `<section class="hero compact">
  <h1>Run a list of identifiers.</h1>
  <p class="tagline">Bulk search</p>
  <p class="lede">Load a JSON file or paste the list. Failed lookups are reported per row and never stop the batch.</p>
</section>

${alert}

<section class="panel">
  <form method="post" action="/bulk" enctype="multipart/form-data" class="stack">
    <input type="hidden" name="style" value="${attr(style)}">
    <div class="field-row">
      <div class="field grow">
        <label for="bulk-file">Identifiers JSON</label>
        <input id="bulk-file" name="file" type="file" accept=".json,.txt,application/json,text/plain">
      </div>
      <div class="field">
        <label for="bulk-release">Release</label>
        <select id="bulk-release" name="release">${releaseOptions(manifest.releases, manifest.default_release)}</select>
      </div>
    </div>
    <div class="field">
      <label for="bulk-payload">Or paste JSON</label>
      <textarea id="bulk-payload" name="payload" rows="6" spellcheck="false" placeholder='["id1", "id2"]'></textarea>
    </div>
    <p class="hint">Accepted shapes: <code>["id1","id2"]</code>, <code>{"identifiers": [...]}</code>, or objects with <code>identifier</code> and optional <code>label</code>. Limit 200 identifiers.</p>
    <div><button type="submit" class="primary">Run bulk search</button></div>
  </form>
</section>${resultPanel}`;
  return layout("Bulk search", content, manifest, null, false, style, staging);
}

export function historyPage(manifest: Manifest, style: Style = "record", staging = false): string {
  const content = `<section class="hero compact">
  <h1>Every lookup in this browser.</h1>
  <p class="tagline">History</p>
  <p class="lede">Stored locally in your browser, never on the server. Click a row to search that identifier again.</p>
</section>
<section class="panel">
  <div class="field">
    <label for="history-filter">Filter</label>
    <input id="history-filter" type="search" placeholder="Filter by identifier or label…" autocomplete="off">
  </div>
  <table class="data-table" id="history-table">
    <thead><tr><th>Searched</th><th>Identifier</th><th>Stays</th><th>Rows</th><th>Label</th></tr></thead>
    <tbody></tbody>
  </table>
  <p class="hint" id="history-empty" hidden>No lookups yet.</p>
</section>`;
  return layout("History", content, manifest, null, false, style, staging);
}

export function helpPage(manifest: Manifest, style: Style = "record", staging = false): string {
  const content = `<section class="hero compact">
  <h1>How to read a pathway.</h1>
  <p class="tagline">Help</p>
</section>
<section class="panel prose">
  <h2>How to search</h2>
  <ol>
    <li>Paste a <code>unique_identifier</code>, <code>stay_ID</code>, or <code>stint_ID</code> into the box and select Search (or press Return).</li>
    <li>The lookup finds every detention and arrest record for that identifier, groups detentions into stays, and orders each stay by book-in time.</li>
    <li>Use Copy to Clipboard for the text currently shown, or Export JSON for the recorded pathway as structured data.</li>
    <li>Recent lookups stay in this browser; use View all… for the full, filterable list.</li>
  </ol>

  <h2>What you cannot change here</h2>
  <p>Arrest location is deliberately not editable in the web app. A public visitor must not be able to print an invented address into a pathway. Other recorded fields can still be corrected with All manual entries…; each correction is tagged inline with the value it replaced, and Export JSON always contains source data only.</p>

  <h2>Separate stays</h2>
  <p>A person may be detained more than once. Each continuous period in custody is a stay, and each facility placement inside a stay is a stint. Multiple stays are labelled <code>[STAY n of total]</code> with the gap between them shown, for example, as <code>=== RELEASED (Paroled); NOT IN ICE CUSTODY FOR 396 days ===</code>. Separate stays are never joined into one pathway.</p>

  <h2>Missing records and impossible dates</h2>
  <p>A stay with no arrest record is labelled <code>NO ARREST RECORD IN THIS DATASET</code>. This is normal: the arrests table covers ICE arrests, so someone transferred from Border Patrol can have detention records and no arrest record. Impossible chronology is retained but marked <code>(DISCREPANCY: …)</code>, and a missing book-out date appears as <code>UNKNOWN - CURRENTLY HELD (?)</code> because the data cannot confirm release.</p>

  <h2>Watermark</h2>
  <p>The first line of every result names the dataset generation and how far its records run, for example <code>dataset-NEW data-up-to=2026-08-06</code>. When a manual entry was applied, a second <code>edited-manual=…</code> line names exactly which fields changed. A block with no such line is source data as published.</p>

  <h2>Bulk search</h2>
  <p>Bulk Search reads a JSON file or pasted list of identifiers, searches each one, shows a per-row status, and exports one JSON bundle or a readable text report. Failures are listed individually and never stop the batch. The web app limits a batch to 200 identifiers.</p>

  <h2>Interpretation</h2>
  <p>This tool reconstructs recorded detention history, not a confirmed deportation outcome or a real-time location. Absence of a record is not evidence of absence in reality. The source data comes from ICE through FOIA requests, has known limitations, and may be revised. Treat pathways as investigative leads and verify consequential claims against primary records.</p>
</section>`;
  return layout("Help", content, manifest, null, false, style, staging);
}

export function datasetsPage(manifest: Manifest, style: Style = "record"): string {
  const articles = manifest.releases.length
    ? manifest.releases
        .map(
          (r) =>
            `<article class="dataset"><h2>${escapeHtml(r.label)}</h2><pre>${escapeHtml(r.watermark)}</pre><p class="hint">${r.identifiers.toLocaleString("en-US")} indexed keys · built ${escapeHtml(manifest.built_at)}</p></article>`,
        )
        .join("\n")
    : '<div class="alert">No datasets are installed. Build the index with website.offline.build_index and upload it to R2.</div>';
  const content = `<section class="hero compact">
  <h1>The data behind the lookups.</h1>
  <p class="tagline">Datasets</p>
  <p class="lede">Datasets are precomputed and served from Cloudflare R2. Visitors cannot replace them; every result carries the watermark of the release actually read.</p>
</section>
<section class="panel">
${articles}
  <p class="hint">Required source files: <code>arrests-latest.parquet</code>, <code>detention-stints-latest.parquet</code>, <code>facilities-latest.parquet</code>. Identifiers are re-salted by the publisher at each release, so an identifier from an older dataset will not resolve in a newer one.</p>
</section>`;
  return layout("Datasets", content, manifest, null, false, style);
}

// Staging keeps the Datasets tab, with the refresh runbook visible.
export function datasetOpsPage(
  manifest: Manifest,
  updateMessage: string | null = null,
  style: Style = "record",
  staging = false,
): string {
  const notice = updateMessage
    ? `<div class="notice" role="status"><pre>${escapeHtml(updateMessage)}</pre></div>`
    : "";
  const rows = manifest.releases.length
    ? manifest.releases
        .map(
          (r) => `<tr>
        <td>${escapeHtml(r.label)}</td>
        <td><code>${escapeHtml(r.watermark)}</code></td>
        <td>${r.identifiers.toLocaleString("en-US")}</td>
        <td>${r.shards.toLocaleString("en-US")}</td>
      </tr>`,
        )
        .join("\n")
    : '<tr><td colspan="4">No datasets installed.</td></tr>';
  // CLI flags are written with real double hyphens; the detector reads them as
  // em-dashes, so this page opts out of that advisory only.
  const content = `<!-- impeccable-disable em-dash-overuse -->
<section class="dataset-ops">
  <div class="panel-head">
    <h1>Dataset status and refresh.</h1>
    <p class="lede">The Worker reads one gzipped JSON shard from R2 per lookup; every result carries the watermark of the release actually read.</p>
  </div>

${notice}

  <div class="data-table-wrap">
    <table class="data-table">
      <thead><tr><th>Release</th><th>Watermark</th><th>Keys</th><th>Shards</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  </div>
  <p class="hint">App v${escapeHtml(manifest.app_version)} · built ${escapeHtml(manifest.built_at)} · default <code>${escapeHtml(manifest.default_release)}</code></p>

  <section class="ops-block">
    <h2>Refresh the data</h2>
    <ol>
      <li>The build reads <code>data-sources.json</code> and downloads a Parquet file when it is missing, older than seven days, or its URL changed since the last build. Use <code>--refresh-data</code> to force a fresh download of all three.</li>
      <li>Rebuild the index:
        <pre>.venv/bin/python -m website.offline.build_index --out website/.build/r2</pre>
      </li>
      <li>Upload to staging first:
        <pre>.venv/bin/python -m website.offline.upload_index \\
  --build-dir website/.build/r2 \\
  --bucket bronx-data-staging \\
  --skip-existing</pre>
      </li>
      <li>Deploy staging:
        <pre>npx wrangler deploy --config website/wrangler.toml --env staging</pre>
      </li>
      <li>Repeat the upload with <code>--bucket bronx-data</code>, then deploy with <code>--env=""</code> when ready.</li>
    </ol>
    <p class="hint"><code>--skip-existing</code> makes an interrupted upload resumable. The Worker caches the manifest for five minutes; redeploy to clear it sooner.</p>
  </section>

  <form method="post" action="/update-check" class="ops-update">
    <input type="hidden" name="identifier" value="">
    <input type="hidden" name="release" value="${attr(manifest.default_release)}">
    <input type="hidden" name="view" value="dataset">
    <input type="hidden" name="style" value="${attr(style)}">
    <button type="submit" class="secondary">Check for Updates…</button>
  </form>
</section>`;
  return layout("Datasets", content, manifest, null, false, style, staging);
}

export function makeHistoryEntry(doc: PathwayDoc, label: string | null = null): Result["historyEntry"] {
  return {
    identifier: doc.identifier,
    searched_at: isoNow(),
    n_stays: doc.totals.stays,
    n_rows: doc.totals.detention_rows,
    label,
  };
}
