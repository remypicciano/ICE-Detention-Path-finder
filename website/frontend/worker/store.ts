/// <reference types="@cloudflare/workers-types" />

// R2 access and pathway lookup. No HTML here.

import {
  formatPathway,
  manualEditLine,
  statusText,
  type Overrides,
  type PathwayDoc,
} from "./format";
import { makeHistoryEntry, type Manifest, type Result } from "./pages";

export interface Env {
  DATA: R2Bucket;
  ASSETS: Fetcher;
}

export type IndexedDoc = PathwayDoc & { _text?: string };
export type AliasDoc = { _alias: string; _focus: string };
export type Shard = { v: number; entries: Record<string, IndexedDoc | AliasDoc> };
export type ShardCache = Map<string, Shard | null>;

const MAX_IDENTIFIER = 200;
const CACHE_MS = 5 * 60 * 1000;
const SHARD_CACHE_MS = 5 * 60 * 1000;
const SHARD_CACHE_LIMIT = 512;
let manifestCache: { value: Manifest; at: number } | undefined;
const shardCache = new Map<string, { value: Shard | null; at: number }>();

export function clean(value: unknown): string {
  return typeof value === "string" ? value.trim().replace(/\s+/g, " ") : "";
}

export function withoutRenderFields<T>(value: T): T {
  if (Array.isArray(value)) return value.map(withoutRenderFields) as T;
  if (value && typeof value === "object") {
    const copy: Record<string, unknown> = {};
    for (const [key, item] of Object.entries(value)) {
      if (!key.startsWith("_")) copy[key] = withoutRenderFields(item);
    }
    return copy as T;
  }
  return value;
}

export async function readObject<T>(env: Env, key: string): Promise<T | null> {
  const object = await env.DATA.get(key);
  if (!object) return null;
  const stream = key.endsWith(".gz")
    ? object.body.pipeThrough(new DecompressionStream("gzip"))
    : object.body;
  return JSON.parse(await new Response(stream).text()) as T;
}

export async function manifest(env: Env): Promise<Manifest> {
  if (manifestCache && Date.now() - manifestCache.at < CACHE_MS) return manifestCache.value;
  const value = await readObject<Manifest>(env, "manifest.json");
  if (!value) throw new Error("The lookup index has not been uploaded to R2 yet.");
  manifestCache = { value, at: Date.now() };
  return value;
}

export function newShardCache(): ShardCache {
  return new Map<string, Shard | null>();
}

async function shardFor(
  env: Env,
  releaseKey: string,
  key: string,
  cache: ShardCache,
): Promise<Shard | null> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(key));
  const hex = Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
  const shardKey = `releases/${releaseKey}/shards/${hex.slice(0, 3)}.json.gz`;
  if (cache.has(shardKey)) return cache.get(shardKey) ?? null;

  const shared = shardCache.get(shardKey);
  if (shared && Date.now() - shared.at < SHARD_CACHE_MS) {
    shardCache.delete(shardKey);
    shardCache.set(shardKey, shared);
    cache.set(shardKey, shared.value);
    return shared.value;
  }

  const shard = await readObject<Shard>(env, shardKey);
  cache.set(shardKey, shard);
  shardCache.set(shardKey, { value: shard, at: Date.now() });
  if (shardCache.size > SHARD_CACHE_LIMIT) {
    const oldest = shardCache.keys().next().value;
    if (oldest) shardCache.delete(oldest);
  }
  return shard;
}

async function findEntry(
  env: Env,
  releaseKey: string,
  key: string,
  cache: ShardCache,
): Promise<IndexedDoc | AliasDoc | null> {
  const shard = await shardFor(env, releaseKey, key, cache);
  return shard?.entries[key] ?? null;
}

function isAlias(entry: IndexedDoc | AliasDoc): entry is AliasDoc {
  return "_alias" in entry;
}

export async function lookup(
  env: Env,
  releaseKey: string,
  rawIdentifier: string,
  overrides: Overrides,
  cache: ShardCache,
): Promise<{ doc: IndexedDoc } | { error: string }> {
  const identifier = clean(rawIdentifier);
  if (!identifier) return { error: "Enter an identifier first." };
  if (identifier.length > MAX_IDENTIFIER) {
    return { error: `Identifier is too long (limit ${MAX_IDENTIFIER} characters).` };
  }

  const base = identifier.split("_", 1)[0] || identifier;
  let entry = await findEntry(env, releaseKey, identifier, cache);
  if (!entry && base !== identifier) entry = await findEntry(env, releaseKey, base, cache);
  if (!entry) {
    return {
      error:
        "Identifier not found in the arrests or detention datasets. It may be invalid or may have been excluded from the locally filtered data.",
    };
  }

  let doc = isAlias(entry) ? await findEntry(env, releaseKey, entry._alias, cache) : entry;
  if (!doc || isAlias(doc)) return { error: "The indexed pathway for this identifier is missing." };
  if (isAlias(entry)) {
    // A scoped lookup must be re-rendered so only the requested stay appears.
    const copy: IndexedDoc = { ...doc, focus_stay_id: entry._focus };
    delete copy._text;
    doc = copy;
  }
  return { doc };
}

function displayText(doc: IndexedDoc, overrides: Overrides, watermark: string): string {
  const body =
    Object.keys(overrides).length === 0 && doc._text && doc.focus_stay_id === null
      ? doc._text
      : formatPathway(doc, overrides);
  return [watermark, manualEditLine(overrides), body].filter(Boolean).join("\n");
}

export function resultFor(doc: IndexedDoc, overrides: Overrides, watermark: string): Result {
  return {
    doc,
    overrides,
    text: displayText(doc, overrides, watermark),
    status: statusText(doc, overrides),
    identifier: doc.identifier,
    historyEntry: makeHistoryEntry(doc),
  };
}

export function releaseWatermark(manifestValue: Manifest, key: string): string {
  return manifestValue.releases.find((r) => r.key === key)?.watermark ?? "";
}
