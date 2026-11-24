// Dependency-free port of ice_detention_pathway.py's text renderer for Workers.
// Text output is intentionally byte-identical to format_pathway() in Python.

declare const process: { env?: Record<string, string | undefined> } | undefined;

const MANUAL_EDIT_MARK = "[MANUAL_EDIT";
const NO_ARREST_NOTE = "NO ARREST RECORD IN THIS DATASET";
const DISCREPANCY_MINIMUM_GAP_MS = 24 * 60 * 60 * 1000;
const MAX_OVERRIDE_LENGTH = 200;

export type Overrides = Record<string, string>;

export const WEB_EDITABLE_FIELDS: readonly string[] = [
  "final_program",
  "book_in_aor",
  "classification",
  "case_status",
  "threat_level",
  "final_order",
  "final_order_date",
  "departed",
  "charge",
] as const;

const EDITABLE_FIELDS: readonly string[] = [
  "location",
  "landmark",
  ...WEB_EDITABLE_FIELDS,
] as const;

type ArrestDoc = {
  date_time: string | null;
  date_only: string | null;
  location: string | null;
  landmark: string | null;
  apprehension_state_fill_source: string | null;
  _landmark_field?: string;
};

type SummaryValues = Record<
  "classification" | "case_status" | "threat_level" | "final_order" |
  "final_order_date" | "departed" | "final_charge",
  string | null
>;

type SummaryDoc = Partial<SummaryValues> | null;

type RowDoc = { book_in: string | null; facility: string | null; book_out: string | null };

type StayDoc = {
  stay_id: string | null;
  arrest: ArrestDoc | null;
  entry_program: string | null;
  entry_aor: string | null;
  release_reason: string | null;
  stint_ids: (string | null)[];
  program_variance: string | null;
  aor_variance: string | null;
  start: string | null;
  end: string | null;
  summary: SummaryDoc;
  rows: RowDoc[];
  _all_programs?: string[];
  _all_aors?: string[];
  _summary_fields?: SummaryValues | null;
};

export type PathwayDoc = {
  schema_version: number;
  exported_at: string;
  identifier: string;
  focus_stay_id: string | null;
  source_fields_absent: string[];
  totals: { stays: number; detention_rows: number; arrests_without_stay: number };
  stays: StayDoc[];
  arrests_without_stay: ArrestDoc[];
};

type Scope = number | null;

function words(value: string | null | undefined): string {
  return value == null ? "" : value.trim().split(/\s+/).filter(Boolean).join(" ");
}

function codePointSlice(value: string, limit: number): string {
  return Array.from(value).slice(0, limit).join("");
}

function overrideKey(scope: Scope, field: string): string {
  return scope === null ? field : `stay-${scope}/${field}`;
}

function editScope(key: string): { scope: Scope; field: string } {
  const index = key.indexOf("/");
  if (index < 0) return { scope: null, field: key };
  const prefix = key.slice(0, index);
  const field = key.slice(index + 1);
  if (!prefix.startsWith("stay-")) return { scope: null, field: key };
  const number = prefix.slice("stay-".length);
  if (!/^\d+$/.test(number) || !EDITABLE_FIELDS.includes(field)) {
    return { scope: null, field: key };
  }
  return { scope: Number(number), field };
}

function editedValue(
  overrides: Overrides | null | undefined,
  field: string,
  datasetValue: string | null | undefined,
  scope: Scope = null,
): string {
  const original = datasetValue || "";
  if (!overrides) return original;
  let manual = words(overrides[overrideKey(scope, field)]);
  if (!manual && scope !== null) manual = words(overrides[field]);
  if (!manual) return original;
  return `${manual} ${MANUAL_EDIT_MARK} original=${original || "(empty)"}]`;
}

function sanitizeOverrides(raw: unknown): Overrides {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {};
  const clean: Overrides = {};
  for (const [key, value] of Object.entries(raw as Record<string, unknown>)) {
    if (typeof value !== "string") continue;
    const { field } = editScope(key);
    if (!WEB_EDITABLE_FIELDS.includes(field)) continue;
    const text = codePointSlice(words(value), MAX_OVERRIDE_LENGTH);
    if (text) clean[key] = text;
  }
  return clean;
}

export { sanitizeOverrides };

export function parseOverrides(raw: string): Overrides {
  try {
    return sanitizeOverrides(JSON.parse(raw));
  } catch {
    return {};
  }
}

export function manualEditLine(overrides: Overrides): string {
  const keys = Object.keys(overrides);
  if (!keys.length) return "";
  const named = keys.map((key) => {
    const parsed = editScope(key);
    return { key, scope: parsed.scope, field: parsed.field };
  });
  named.sort((a, b) => {
    const aScoped = a.scope !== null ? 1 : 0;
    const bScoped = b.scope !== null ? 1 : 0;
    if (aScoped !== bScoped) return aScoped - bScoped;
    if ((a.scope ?? 0) !== (b.scope ?? 0)) return (a.scope ?? 0) - (b.scope ?? 0);
    const ai = WEB_EDITABLE_FIELDS.indexOf(a.field);
    const bi = WEB_EDITABLE_FIELDS.indexOf(b.field);
    return (ai < 0 ? Number.MAX_SAFE_INTEGER : ai) - (bi < 0 ? Number.MAX_SAFE_INTEGER : bi);
  });
  return "edited-manual=" + named.map((item) => item.key).join(",");
}

function parseUtcMs(value: string): number {
  if (/[+-]\d{2}:\d{2}$/.test(value) || value.endsWith("Z")) return Date.parse(value);
  return Date.parse(value + "Z");
}

function utcIso(value: string): string {
  return new Date(parseUtcMs(value)).toISOString();
}

function formatTimestamp(value: string | null, eventLabel: string): string {
  if (value === null) {
    return eventLabel === "book-out" ? "UNKNOWN - CURRENTLY HELD (?)" : "UNKNOWN UTC";
  }
  return `${utcIso(value).slice(0, 19).replace("T", " ")} UTC`;
}

function dateOnlyMs(value: string): number {
  const parts = value.slice(0, 10).split("-").map(Number);
  return Date.UTC(parts[0], parts[1] - 1, parts[2]);
}

function exceedsGap(earlier: string, later: string): boolean {
  return parseUtcMs(later) - parseUtcMs(earlier) >= DISCREPANCY_MINIMUM_GAP_MS;
}

function cleanLocation(value: string | null | undefined): string {
  return words(value) || "UNKNOWN DETENTION CENTER";
}

function cleanArrestLocation(value: string | null | undefined): string {
  return words(value) || "UNKNOWN ARREST LOCATION";
}

function durationText(start: string | null, end: string | null): string {
  if (!start || !end) return "an unknown period";
  const ms = parseUtcMs(end) - parseUtcMs(start);
  const dayMs = DISCREPANCY_MINIMUM_GAP_MS;
  const days = Math.floor(ms / dayMs);
  if (days < 0) return `a negative span of ${Math.abs(days)} days`;
  if (days) return `${days} days`;
  const hours = Math.floor(ms / 3_600_000);
  if (hours) return `${hours} hours`;
  return `${Math.max(Math.floor(ms / 60_000), 1)} minutes`;
}

function formatTimeline(rows: RowDoc[]): string {
  const segments: string[] = [];
  let previousBookOut: string | null = null;
  for (const [bookIn, facility, bookOut] of rows.map(
    (row): [string | null, string | null, string | null] => [row.book_in, row.facility, row.book_out],
  )) {
    const warnings: string[] = [];
    if (bookIn !== null && bookOut !== null && exceedsGap(bookOut, bookIn)) {
      warnings.push("book-out is before book-in");
    }
    if (bookIn !== null && previousBookOut !== null && exceedsGap(bookIn, previousBookOut)) {
      warnings.push("detention begins before previous book-out");
    }
    let segment =
      `[Book-in: ${formatTimestamp(bookIn, "book-in")}]` +
      `[Book-out: ${formatTimestamp(bookOut, "book-out")}]` +
      `[Facility: ${cleanLocation(facility)}]`;
    if (warnings.length) segment = `(DISCREPANCY: ${warnings.join("; ")}) ${segment}`;
    segments.push(segment);
    if (bookOut !== null && (previousBookOut === null || parseUtcMs(bookOut) > parseUtcMs(previousBookOut))) {
      previousBookOut = bookOut;
    }
  }
  return segments.join(" -> ");
}

function splitProvenance(label: string, value: string): string[] {
  const out: string[] = [];
  const segments = value.includes(";") ? value.split(";") : [value];
  for (const raw of segments) {
    const segment = raw.trim();
    if (segment) out.push(`${label}: ${segment}`);
  }
  return out;
}

function arrestProvenance(arrest: ArrestDoc, overrides: Overrides | null | undefined, scope: Scope): string[] {
  const pairs: string[] = [];
  const label = arrest._landmark_field || "event_landmark";
  const landmark = editedValue(overrides, "landmark", arrest.landmark, scope);
  pairs.push(...splitProvenance(label, landmark));
  pairs.push(...splitProvenance("apprehension_state_fill_source", arrest.apprehension_state_fill_source || ""));
  return pairs;
}

function formatFullTimeline(
  arrest: ArrestDoc | null,
  rows: RowDoc[],
  overrides: Overrides | null | undefined,
  scope: Scope = null,
): string {
  if (arrest === null) return `${NO_ARREST_NOTE} -> ${formatTimeline(rows)}`;

  const warnings: string[] = [];
  const firstBookIn = rows.find((row) => row.book_in !== null)?.book_in ?? null;
  if (firstBookIn !== null) {
    if (arrest.date_time !== null) {
      if (exceedsGap(firstBookIn, arrest.date_time)) {
        warnings.push("arrest date is after first detention book-in");
      }
    } else if (arrest.date_only !== null) {
      const bookInDate = utcIso(firstBookIn).slice(0, 10);
      if (dateOnlyMs(arrest.date_only) - dateOnlyMs(bookInDate) >= DISCREPANCY_MINIMUM_GAP_MS) {
        warnings.push("arrest date is after first detention book-in");
      }
    }
  }

  let arrestDate: string;
  if (arrest.date_time !== null) arrestDate = formatTimestamp(arrest.date_time, "arrest");
  else if (arrest.date_only !== null) arrestDate = arrest.date_only.slice(0, 10);
  else arrestDate = "UNKNOWN ARREST DATE";

  const datasetLocation = cleanArrestLocation(arrest.location);
  const shownLocation = editedValue(overrides, "location", datasetLocation, scope);
  let arrestSegment = `${arrestDate}, ${shownLocation}`;
  if (warnings.length) arrestSegment = `(DISCREPANCY: ${warnings.join("; ")}) ${arrestSegment}`;
  if (!rows.length) return `${arrestSegment} -> NO DETENTION RECORD IN THIS DATASET`;
  return `${arrestSegment} -> ${formatTimeline(rows)}`;
}

function formatGap(previous: StayDoc, current: StayDoc): string {
  if (!previous.end || !current.start) return "=== SEPARATE STAY; GAP NOT MEASURABLE ===";
  const span = durationText(previous.end, current.start);
  if (previous.release_reason) {
    return `=== RELEASED (${words(previous.release_reason)}); NOT IN ICE CUSTODY FOR ${span} ===`;
  }
  return `=== NOT IN ICE CUSTODY FOR ${span} ===`;
}

function formatArrestFields(stay: StayDoc, overrides: Overrides | null | undefined, scope: Scope): string {
  if (!stay.arrest) return "";
  const pairs = arrestProvenance(stay.arrest, overrides, scope);
  return pairs.length ? "[arrest — " + pairs.join("; ") + "]" : "";
}

function allValues(stay: StayDoc, which: "programs" | "aors"): string[] {
  const supplied = which === "programs" ? stay._all_programs : stay._all_aors;
  if (supplied) return supplied;
  const entry = which === "programs" ? stay.entry_program : stay.entry_aor;
  const variance = which === "programs" ? stay.program_variance : stay.aor_variance;
  const result: string[] = [];
  if (entry) result.push(entry);
  for (const value of variance ? variance.split("; ") : []) {
    if (value && !result.includes(value)) result.push(value);
  }
  return result;
}

function differingValues(entry: string | null, values: string[]): string[] {
  const seen: string[] = [];
  for (const value of values) {
    if (value && value !== entry && !seen.includes(value)) seen.push(value);
  }
  return seen;
}

const SUMMARY_PAIRS: readonly [string, keyof NonNullable<SummaryDoc>][] = [
  ["classification", "classification"],
  ["case_status", "case_status"],
  ["threat_level", "threat_level"],
  ["final_order", "final_order"],
  ["final_order_date", "final_order_date"],
  ["departed", "departed"],
  ["charge", "final_charge"],
] as const;

function formatStayFields(stay: StayDoc, overrides: Overrides | null | undefined, scope: Scope): string {
  const lines: string[] = [];
  const first: string[] = [];
  const program = editedValue(overrides, "final_program", stay.entry_program, scope);
  if (program) first.push(`final_program: ${program}`);
  const aor = editedValue(overrides, "book_in_aor", stay.entry_aor, scope);
  if (aor) first.push(`book_in_aor: ${aor}`);
  if (first.length) lines.push("[first stint — " + first.join("; ") + "]");

  const differing: string[] = [];
  for (const value of differingValues(stay.entry_program, allValues(stay, "programs"))) {
    differing.push(`final_program: ${value}`);
  }
  for (const value of differingValues(stay.entry_aor, allValues(stay, "aors"))) {
    differing.push(`book_in_aor: ${value}`);
  }
  if (differing.length) lines.push("[stint fields — " + differing.join("; ") + "]");

  if (stay.summary) {
    const values = stay._summary_fields ?? stay.summary;
    const summaryPairs: string[] = [];
    for (const [label, key] of SUMMARY_PAIRS) {
      const shown = editedValue(overrides, label, values[key], scope);
      if (shown) summaryPairs.push(`${label}: ${shown}`);
    }
    if (summaryPairs.length) lines.push("[last stint — " + summaryPairs.join("; ") + "]");
  }
  return lines.join("\n");
}

function stayDetailLines(stay: StayDoc, overrides: Overrides | null | undefined, scope: Scope): string[] {
  return [formatArrestFields(stay, overrides, scope), formatStayFields(stay, overrides, scope)].filter(Boolean);
}

function formatContextStay(stay: StayDoc): string {
  const start = stay.start ? formatTimestamp(stay.start, "book-in") : "UNKNOWN";
  const end = stay.end ? formatTimestamp(stay.end, "book-out") : "UNKNOWN";
  const label = stay.rows.length ? cleanLocation(stay.rows[0].facility) : "UNKNOWN DETENTION CENTER";
  return `  [CONTEXT — another stay for this person: ${start} -> ${end}, ${label}]`;
}

export function formatPathway(doc: PathwayDoc, overrides: Overrides): string {
  if (!doc.stays.length) {
    return doc.arrests_without_stay
      .map((arrest) => formatFullTimeline(arrest, [], overrides))
      .join("\n");
  }

  const total = doc.stays.length;
  const parts: string[] = [];
  if (doc.focus_stay_id !== null) {
    doc.stays.forEach((stay, index) => {
      const number = index + 1;
      if (stay.stay_id === doc.focus_stay_id) {
        parts.push(`[STAY ${number} of ${total}] ` + formatFullTimeline(stay.arrest, stay.rows, overrides, number));
        parts.push(...stayDetailLines(stay, overrides, number));
      } else {
        parts.push(formatContextStay(stay));
      }
    });
  } else {
    let previous: StayDoc | null = null;
    doc.stays.forEach((stay, index) => {
      const number = index + 1;
      if (previous) parts.push(formatGap(previous, stay));
      const rendered = formatFullTimeline(stay.arrest, stay.rows, overrides, number);
      parts.push(total === 1 ? rendered : `[STAY ${number} of ${total}] ${rendered}`);
      parts.push(...stayDetailLines(stay, overrides, number));
      previous = stay;
    });
  }

  for (const arrest of doc.arrests_without_stay) {
    parts.push("[ARREST WITH NO RECORDED DETENTION] " + formatFullTimeline(arrest, [], overrides));
  }
  return parts.join("\n");
}

export function statusText(doc: PathwayDoc, overrides: Overrides): string {
  let summary = `Found ${doc.totals.detention_rows} detention row(s) across ${doc.totals.stays} stay(s).`;
  if (doc.totals.stays > 1) {
    summary += " Separate stays are shown separately; they are not one continuous detention.";
  }
  if (doc.source_fields_absent.length) {
    summary += " Not in this dataset release: " + doc.source_fields_absent.join(", ") +
      " (absent from the source, not unknown).";
  }
  const keys = Object.keys(overrides);
  if (keys.length) {
    summary += " Manual entries applied: " + keys.sort().join(", ") +
      " — each marked in place with the value it replaced.";
  } else {
    summary += " No manual entries — this is source data as published.";
  }
  return summary;
}

// Small dependency-free self-test, enabled by BRONX_FORMAT_SELFTEST=1.
if (typeof process !== "undefined" && process?.env?.BRONX_FORMAT_SELFTEST) {
  const fixture: PathwayDoc = {
    schema_version: 2,
    exported_at: "2026-01-01T00:00:00Z",
    identifier: "X-1",
    focus_stay_id: null,
    source_fields_absent: ["charge"],
    totals: { stays: 2, detention_rows: 2, arrests_without_stay: 0 },
    stays: [
      {
        stay_id: "s1",
        arrest: {
          date_time: "2026-08-01T10:00:00Z", date_only: null, location: "DALLAS TX",
          landmark: "PRISON CAP PROGRAM; FEDERAL BOP PRISON CASES",
          apprehension_state_fill_source: "TX", _landmark_field: "event_landmark",
        },
        entry_program: "ERO", entry_aor: "AOR-1", release_reason: "RELEASED",
        stint_ids: ["st1"], program_variance: null, aor_variance: null,
        start: "2026-08-01T11:00:00Z", end: "2026-08-03T11:00:00Z",
        summary: {
          classification: "NONE", case_status: "PENDING", threat_level: "LOW",
          final_order: null, final_order_date: null, departed: null, final_charge: "CHARGE",
        },
        rows: [{ book_in: "2026-08-01T11:00:00Z", facility: "DET CENTER", book_out: "2026-08-03T11:00:00Z" }],
      },
      {
        stay_id: "s2", arrest: null, entry_program: "ERO", entry_aor: "AOR-2",
        release_reason: null, stint_ids: ["st2"], program_variance: null, aor_variance: null,
        start: "2026-08-10T11:00:00Z", end: "2026-08-11T11:00:00Z", summary: null,
        rows: [{ book_in: "2026-08-10T11:00:00Z", facility: "OTHER", book_out: "2026-08-11T11:00:00Z" }],
      },
    ],
    arrests_without_stay: [],
  };

  const text = formatPathway(fixture, {});
  console.assert(text.includes("event_landmark: PRISON CAP PROGRAM; event_landmark: FEDERAL BOP PRISON CASES"), "landmark split");
  console.assert(text.includes("=== RELEASED (RELEASED); NOT IN ICE CUSTODY FOR 7 days ==="), "gap line");
  console.assert(text.includes("[last stint — classification: NONE; case_status: PENDING; threat_level: LOW; charge: CHARGE]"), "summary labels");
  console.assert(manualEditLine({ "stay-1/classification": "EDITED" }) === "edited-manual=stay-1/classification", "edit line");
  console.assert(
    formatPathway(fixture, { "stay-1/classification": "EDITED" }).includes("classification: EDITED [MANUAL_EDIT original=NONE]"),
    "manual edit mark",
  );
  console.assert(
    statusText(fixture, {}) ===
      "Found 2 detention row(s) across 2 stay(s). Separate stays are shown separately; they are not one continuous detention. Not in this dataset release: charge (absent from the source, not unknown). No manual entries — this is source data as published.",
    "status text",
  );
  console.assert(Object.keys(sanitizeOverrides({ landmarx: "x", classification: "  OK  " })).join() === "classification", "sanitize");
}
