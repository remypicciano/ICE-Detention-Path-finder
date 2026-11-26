/// <reference types="@cloudflare/workers-types" />

// Request routing, form parsing, and response building. Data access lives in
// store.ts; HTML lives in pages.ts.

import { parseOverrides } from "./format";
import {
  bulkPage,
  datasetOpsPage,
  datasetsPage,
  helpPage,
  historyPage,
  indexPage,
  lookupPage,
  makeHistoryEntry,
  tablePage,
  type BulkResult,
  type IndexOptions,
  type Manifest,
  type Result,
} from "./pages";
import {
  clean,
  lookup,
  manifest,
  newShardCache,
  readObject,
  releaseWatermark,
  resultFor,
  withoutRenderFields,
  type Env,
  type IndexedDoc,
} from "./store";

const MAX_BULK = 200;
const REPO = "remypicciano/ICE-Detention-Path-finder";
const hits = new Map<string, number[]>();

function html(body: string, status = 200): Response {
  return new Response(body, {
    status,
    headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" },
  });
}

function text(value: string, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(value, {
    status,
    headers: { "content-type": "text/plain; charset=utf-8", ...headers },
  });
}

function json(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value) + "\n", {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
  });
}

function redirect(path: string): Response {
  return new Response(null, { status: 302, headers: { location: path } });
}

function rateLimit(request: Request, kind: "search" | "bulk", limit: number): boolean {
  const ip = request.headers.get("cf-connecting-ip") || request.headers.get("x-forwarded-for") || "unknown";
  const key = `${kind}:${ip}`;
  const now = Date.now();
  const recent = (hits.get(key) || []).filter((when) => now - when < 60_000);
  if (recent.length >= limit) {
    hits.set(key, recent);
    return false;
  }
  recent.push(now);
  hits.set(key, recent);
  if (hits.size > 10_000) hits.clear();
  return true;
}

function isStaging(url: URL): boolean {
  return url.hostname === "bronx-staging.remypicciano.me" || url.hostname.startsWith("bronx-staging.");
}

function parseIdentifiers(raw: string): { identifier: string; label: string | null }[] {
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch (error) {
    throw new Error(`Not valid JSON: ${(error as Error).message}`);
  }
  if (data && typeof data === "object" && !Array.isArray(data)) {
    data = (data as Record<string, unknown>).identifiers;
  }
  if (!Array.isArray(data)) {
    throw new Error(
      "Expected a JSON array of identifiers, or an object with an 'identifiers' array.",
    );
  }
  if (data.length > MAX_BULK) throw new Error(`Bulk search is limited to ${MAX_BULK} identifiers.`);
  const items = data.map((item, index) => {
    if (typeof item === "string") return { identifier: item, label: null };
    if (item && typeof item === "object" && typeof (item as { identifier?: unknown }).identifier === "string") {
      const record = item as { identifier: string; label?: unknown };
      return {
        identifier: record.identifier,
        label: typeof record.label === "string" ? record.label : null,
      };
    }
    throw new Error(
      `Item ${index + 1} is not an identifier string or an object with an 'identifier' field.`,
    );
  });
  if (!items.length) throw new Error("The file contains no identifiers.");
  return items;
}

function bulkJsonBundle(results: BulkResult[]): string {
  const found = results.filter((r) => r.status === "ok").length;
  return JSON.stringify(
    {
      schema_version: 1,
      exported_at: new Date().toISOString().replace(/\.\d{3}Z$/, "Z"),
      total: results.length,
      found,
      failed: results.length - found,
      results: results.map(({ text: _text, ...rest }) => withoutRenderFields(rest)),
    },
    null,
    2,
  ) + "\n";
}

function bulkTextBundle(results: BulkResult[]): string {
  const sections = results.map((result) => {
    const heading = `===== ${result.identifier}${result.label ? ` (${result.label})` : ""} =====`;
    const body =
      result.status === "ok"
        ? result.text || "(no pathway)"
        : `ERROR: ${result.error || result.detail || result.status}`;
    return `${heading}\n${body}`;
  });
  const found = results.filter((r) => r.status === "ok").length;
  sections.push(
    `===== SUMMARY =====\nFound: ${found}    Failed: ${results.length - found}    Total: ${results.length}`,
  );
  return sections.join("\n\n") + "\n";
}

async function updateMessage(current: string): Promise<string> {
  try {
    const response = await fetch(`https://api.github.com/repos/${REPO}/releases/latest`, {
      headers: {
        accept: "application/vnd.github+json",
        "user-agent": "ice-detention-pathway-web",
        "x-github-api-version": "2022-11-28",
      },
    });
    if (!response.ok) return `Update check failed: GitHub returned HTTP ${response.status}.`;
    const payload = (await response.json()) as Record<string, unknown>;
    const tag = typeof payload.tag_name === "string" ? payload.tag_name.trim().replace(/^v/, "") : "";
    if (!tag) return "Update check failed: GitHub's response named no release tag.";
    const page = typeof payload.html_url === "string" ? payload.html_url : `https://github.com/${REPO}/releases`;
    const published = typeof payload.published_at === "string" ? payload.published_at : "";
    const lines =
      tag === current
        ? [`UP TO DATE: ${current} is the latest release.`]
        : [`UPDATE AVAILABLE: ${tag} (you have ${current})`];
    lines.push(`Release page: ${page}`);
    if (published) lines.push(`Published: ${published}`);
    lines.push("The website is served from the project source; it is not replaced by a desktop asset.");
    return lines.join("\n");
  } catch (error) {
    return `Update check failed: ${(error as Error).message}`;
  }
}

type LookupOutcome =
  | { ok: true; result: Result; releaseKey: string; identifier: string }
  | { ok: false; error: string; status: number };

async function lookupFromForm(
  request: Request,
  env: Env,
  manifestValue: Manifest,
  form: FormData,
): Promise<LookupOutcome> {
  if (!rateLimit(request, "search", 60)) {
    return { ok: false, error: "Too many requests. Please wait a moment.", status: 429 };
  }
  const identifier = clean(form.get("identifier"));
  const releaseKey = clean(form.get("release")) || manifestValue.default_release;
  const overrides = parseOverrides(String(form.get("overrides") || "{}"));
  const found = await lookup(env, releaseKey, identifier, overrides, newShardCache());
  if ("error" in found) return { ok: false, error: found.error, status: 200 };
  const result = resultFor(found.doc, overrides, releaseWatermark(manifestValue, releaseKey));
  return { ok: true, result, releaseKey, identifier };
}

async function handle(request: Request, env: Env): Promise<Response> {
  const url = new URL(request.url);
  const path = url.pathname;
  const single = isStaging(url);
  // One committed direction per environment; legacy style links are inert.
  const styleFor = (_value: unknown) => (single ? ("film" as const) : ("record" as const));
  const style = styleFor(url.searchParams.get("style"));

  if (path === "/health") return text("ok");
  if (path === "/style.css" || path === "/app.js" || path === "/favicon.ico") {
    return env.ASSETS.fetch(request);
  }

  let manifestValue: Manifest;
  try {
    manifestValue = await manifest(env);
  } catch (error) {
    return text((error as Error).message, 503);
  }

  const defaultResult = (): Pick<IndexOptions, "result" | "status" | "updateMessage"> => ({
    result: null,
    status: manifestValue.releases.length
      ? "Paste an identifier and select Search."
      : "No datasets yet — the index must be uploaded to R2.",
    updateMessage: null,
  });
  const renderIndex = (options: IndexOptions): string =>
    single ? lookupPage(options) : indexPage(options);
  const renderForView = (options: IndexOptions, view: string): string =>
    single && view === "table" ? tablePage(options) : renderIndex(options);

  if (request.method === "GET" && path === "/") {
    const defaults = defaultResult();
    const preset = url.searchParams.get("q") || "";
    return html(
      renderIndex({
        manifest: manifestValue,
        ...defaults,
        status: single && !preset ? "" : defaults.status,
        error: null,
        enteredIdentifier: preset,
        enteredRelease: url.searchParams.get("release") || manifestValue.default_release,
        style,
        revealed: !single || Boolean(preset),
      }),
    );
  }

  if (request.method === "GET" && single && path === "/table") {
    const query = url.searchParams.get("q") || "";
    const releaseKey = url.searchParams.get("release") || manifestValue.default_release;
    if (!query) {
      return html(
        tablePage({
          manifest: manifestValue,
          ...defaultResult(),
          error: null,
          enteredIdentifier: "",
          enteredRelease: releaseKey,
          style,
        }),
      );
    }
    const form = new FormData();
    form.set("identifier", query);
    form.set("release", releaseKey);
    form.set("overrides", url.searchParams.get("overrides") || "{}");
    const outcome = await lookupFromForm(request, env, manifestValue, form);
    if (!outcome.ok) {
      return html(
        tablePage({
          manifest: manifestValue,
          result: null,
          status: "Search failed.",
          error: outcome.error,
          updateMessage: null,
          enteredIdentifier: query,
          enteredRelease: releaseKey,
          style,
        }),
        outcome.status,
      );
    }
    return html(
      tablePage({
        manifest: manifestValue,
        result: outcome.result,
        status: outcome.result.status,
        error: null,
        updateMessage: null,
        enteredIdentifier: outcome.identifier,
        enteredRelease: outcome.releaseKey,
        style,
      }),
    );
  }

  if (request.method === "GET" && single && (path === "/guided" || path === "/workbench")) {
    return redirect("/");
  }
  if (request.method === "GET" && single && path === "/dataset") {
    return redirect("/datasets");
  }

  if (request.method === "GET" && path === "/demo") {
    const demo = await readObject<{ doc: IndexedDoc; text: string; status: string }>(env, "demo.json");
    if (!demo) return text("Demo data is not installed.", 404);
    const result: Result = {
      doc: demo.doc,
      overrides: {},
      text: demo.text,
      status: demo.status,
      identifier: demo.doc.identifier,
      historyEntry: makeHistoryEntry(demo.doc),
    };
    return html(
      renderIndex({
        manifest: manifestValue,
        result,
        status: demo.status,
        error: null,
        updateMessage: null,
        enteredIdentifier: "",
        enteredRelease: manifestValue.default_release,
        style,
      }),
    );
  }

  if (request.method === "GET" && path === "/history") return html(historyPage(manifestValue, style, single));
  if (request.method === "GET" && path === "/help") return html(helpPage(manifestValue, style, single));
  if (request.method === "GET" && path === "/datasets") {
    return html(
      single
        ? datasetOpsPage(manifestValue, null, style, single)
        : datasetsPage(manifestValue, style),
    );
  }
  if (request.method === "GET" && path === "/bulk") {
    return html(bulkPage(manifestValue, null, null, null, null, style, single));
  }

  if (request.method === "POST" && path === "/api/lookup") {
    let form: FormData;
    if ((request.headers.get("content-type") || "").includes("application/json")) {
      const data = (await request.json()) as Record<string, unknown>;
      form = new FormData();
      form.set("identifier", String(data.identifier ?? ""));
      form.set("release", String(data.release ?? ""));
      form.set(
        "overrides",
        typeof data.overrides === "string"
          ? data.overrides
          : JSON.stringify(data.overrides ?? {}),
      );
    } else {
      form = await request.formData();
    }
    const outcome = await lookupFromForm(request, env, manifestValue, form);
    if (!outcome.ok) return json({ error: outcome.error }, outcome.status);
    const { result, releaseKey, identifier } = outcome;
    return json({
      ok: true,
      identifier,
      release: releaseKey,
      text: result.text,
      status: result.status,
      stays: result.doc.totals.stays,
      rows: result.doc.totals.detention_rows,
      overrides: result.overrides,
      historyEntry: result.historyEntry,
    });
  }

  if (request.method === "POST" && (path === "/search" || path === "/manual")) {
    const form = await request.formData();
    const formStyle = styleFor(form.get("style"));
    const view = clean(form.get("view"));
    const outcome = await lookupFromForm(request, env, manifestValue, form);
    if (!outcome.ok) {
      return html(
        renderForView(
          {
            manifest: manifestValue,
            result: null,
            status: "Search failed.",
            error: outcome.error,
            updateMessage: null,
            enteredIdentifier: clean(form.get("identifier")),
            enteredRelease: clean(form.get("release")) || manifestValue.default_release,
            style: formStyle,
          },
          view,
        ),
        outcome.status,
      );
    }
    return html(
      renderForView(
        {
          manifest: manifestValue,
          result: outcome.result,
          status: outcome.result.status,
          error: null,
          updateMessage: null,
          enteredIdentifier: outcome.identifier,
          enteredRelease: outcome.releaseKey,
          style: formStyle,
        },
        view,
      ),
    );
  }

  if (request.method === "POST" && path === "/export-json") {
    const form = await request.formData();
    const identifier = clean(form.get("identifier"));
    const releaseKey = clean(form.get("release")) || manifestValue.default_release;
    const found = await lookup(env, releaseKey, identifier, {}, newShardCache());
    if ("error" in found) return text(found.error, 400);
    const copy = withoutRenderFields(found.doc);
    const filename = identifier.replace(/[^A-Za-z0-9_-]+/g, "-").replace(/^-|-$/g, "").slice(0, 80) || "pathway";
    return new Response(JSON.stringify(copy, null, 2) + "\n", {
      headers: {
        "content-type": "application/json",
        "content-disposition": `attachment; filename="${filename}.json"`,
      },
    });
  }

  if (request.method === "POST" && path === "/update-check") {
    const form = await request.formData();
    const view = clean(form.get("view"));
    const formStyle = styleFor(form.get("style"));
    const message = await updateMessage(manifestValue.app_version);
    if (single && view === "dataset") {
      return html(datasetOpsPage(manifestValue, message, formStyle, single));
    }
    if (single && view === "table") {
      return html(
        tablePage({
          manifest: manifestValue,
          result: null,
          status: "Update check complete.",
          error: null,
          updateMessage: message,
          enteredIdentifier: clean(form.get("identifier")),
          enteredRelease: clean(form.get("release")) || manifestValue.default_release,
          style: formStyle,
        }),
      );
    }
    return html(
      renderIndex({
        manifest: manifestValue,
        ...defaultResult(),
        status: "Update check complete.",
        updateMessage: message,
        enteredIdentifier: clean(form.get("identifier")),
        enteredRelease: clean(form.get("release")) || manifestValue.default_release,
        style: formStyle,
      }),
    );
  }

  if (request.method === "POST" && path === "/bulk") {
    if (!rateLimit(request, "bulk", 8)) return text("Too many requests. Please wait a moment.", 429);
    const form = await request.formData();
    const formStyle = styleFor(form.get("style"));
    const file = form.get("file");
    let raw = "";
    if (file && typeof file !== "string" && file.size > 0) raw = await file.text();
    if (!raw.trim()) raw = String(form.get("payload") || "");
    const releaseKey = clean(form.get("release")) || manifestValue.default_release;
    let items: { identifier: string; label: string | null }[];
    try {
      if (!raw.trim()) throw new Error("Upload a JSON file or paste the identifier list first.");
      items = parseIdentifiers(raw);
    } catch (error) {
      return html(bulkPage(manifestValue, null, null, (error as Error).message, null, formStyle, single));
    }

    const cache = newShardCache();
    const results: BulkResult[] = [];
    for (const item of items) {
      const found = await lookup(env, releaseKey, item.identifier, {}, cache);
      if ("error" in found) {
        results.push({
          identifier: item.identifier,
          label: item.label,
          status: "error",
          pathway: null,
          text: null,
          detail: `error: ${found.error}`,
          error: found.error,
          history_entry: null,
        });
      } else {
        const result = resultFor(found.doc, {}, releaseWatermark(manifestValue, releaseKey));
        results.push({
          identifier: found.doc.identifier,
          label: item.label,
          status: "ok",
          pathway: found.doc,
          text: result.text,
          detail: `${found.doc.totals.stays} stays, ${found.doc.totals.detention_rows} rows`,
          history_entry: result.historyEntry,
        });
      }
    }

    const token = crypto.randomUUID().replaceAll("-", "").slice(0, 12);
    await env.DATA.put(`bulk/${token}.json`, JSON.stringify(results), {
      httpMetadata: { contentType: "application/json" },
    });
    const foundCount = results.filter((r) => r.status === "ok").length;
    return html(
      bulkPage(
        manifestValue,
        results,
        { found: foundCount, failed: results.length - foundCount, total: results.length },
        null,
        token,
        formStyle,
        single,
      ),
    );
  }

  if (request.method === "GET" && /^\/bulk\/[a-f0-9]+\/export\.(json|txt)$/.test(path)) {
    const match = path.match(/^\/bulk\/([a-f0-9]+)\/export\.(json|txt)$/);
    if (!match) return text("Not found", 404);
    const results = await readObject<BulkResult[]>(env, `bulk/${match[1]}.json`);
    if (!results) return text("This bulk run has expired. Run it again.", 404);
    if (match[2] === "json") {
      return new Response(bulkJsonBundle(results), {
        headers: {
          "content-type": "application/json",
          "content-disposition": 'attachment; filename="bulk-results.json"',
        },
      });
    }
    return text(bulkTextBundle(results), 200, {
      "content-disposition": 'attachment; filename="bulk-results.txt"',
    });
  }

  return text("Not found", 404);
}

export default {
  fetch(request: Request, env: Env): Promise<Response> {
    return handle(request, env).catch((error: unknown) => {
      console.error("bronx worker error", error);
      return text(`Server error: ${(error as Error).message}`, 500);
    });
  },
} satisfies ExportedHandler<Env>;
