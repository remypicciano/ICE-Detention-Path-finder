# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Inferred from the existing desktop tool: people tracing one person's ICE detention pathway by identifier, stay ID, or stint ID, then copying or exporting the result. Family members, advocates, and case researchers may all fit this job; exact audience mix is unconfirmed.

## Product Purpose

Web version of the existing ICE Detention Pathway desktop app. It lets a visitor enter an identifier and read the same pathway text the desktop tool produces, with manual-entry rendering, bulk lookup, JSON export, browser-local history, demo, dataset watermarks, and update check. Success means the visitor gets the correct pathway quickly without installing software.

## Positioning

The lookup engine is precomputed offline from the same Parquet datasets and Parquet-derived Pathway objects as the desktop app. A free Cloudflare Worker reads one gzipped JSON shard from R2 per search and renders it in TypeScript. No public API, no per-request Python/DuckDB, no container, no always-on server.

## Operating Context

Production at `https://bronx.remypicciano.me`; staging at `https://bronx-staging.remypicciano.me`. Dataset releases carry explicit watermarks (`dataset-NEW data-up-to=2026-08-06`, `dataset-OLD data-up-to=2026-09-17`). Update check points to GitHub releases. The demo identifier is `UFAKE-0001`; app version is `2.1.6`.

## Capabilities and Constraints

- Preserve the functionality of the non-website software, including output text and data semantics.
- Identifier, stay ID, and stint ID search across release shards; alias focus for stay/stint IDs.
- Manual entries for all desktop fields except arrest location; `[MANUAL_EDIT original=…]` tagging preserved.
- Bulk search capped at 200 identifiers; exports stored as temporary R2 objects.
- Copy and source-only JSON export.
- History lives only in browser `localStorage` (`bronx.history.v1`).
- Free-tier Cloudflare only: Workers Free + R2 Free. No paid Workers, containers, or public API.
- Staging and production use separate Workers and R2 buckets.
- Staging uses one lookup panel and keeps the Datasets tab; production keeps the two-column workbench.
- Visible UI copy and rendered pathway text are in scope for preservation; redesign is visual only.

## Brand Commitments

Name and product language come from the existing desktop app and current web UI. The user explicitly requires keeping existing functionality, data, and copy. No new voice, logo, claim, testimonial, or metric may be invented.

## Evidence on Hand

`ice_detention_pathway.py` is the lookup and formatter source of truth. `website/frontend/worker/format.ts` is a verified byte-identical port. `website/offline/build_index.py` rebuilt all releases into `website/.build/r2`: 10,586,760 keys, 8,192 gzipped shards, ~1 GB. Test identifier `00001004d1cb0b011bdbcd2b81c4cd56569ca759` resolves in the NEW release. Demo `UFAKE-0001` is fabricated and labeled as such.

## Product Principles

- Preserve desktop behavior and rendered output exactly.
- Make the correct pathway readable in one fast lookup.
- Keep data provenance and watermarks visible.
- Keep private search history in the browser.
- Stay inside the free Cloudflare tier.

## Accessibility & Inclusion

Not specified beyond normal web expectations; preserve keyboard access, focus visibility, semantic controls, and readable text/contrast.
