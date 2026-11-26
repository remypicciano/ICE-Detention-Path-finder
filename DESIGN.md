---
name: Bronx — ICE Detention Pathway
description: Production is a ruled record workbench; staging is a flat film ledger that opens from one search bar.
colors:
  ground: "#0b0e10"
  panel: "#111619"
  ink: "#dbe3e4"
  muted: "#98a4a7"
  rule: "#2a3235"
  focus: "#7be7ef"
  selection: "#234348"
  selection-ink: "#f2ffff"
  production-bone: "#e8ebe9"
  production-paper: "#f8f9f7"
  production-ink: "#171a18"
  production-graphite: "#4b504d"
  production-rule: "#c3c8c4"
  production-vermilion: "#b23a22"
  production-vermilion-deep: "#8e2d19"
  production-selection: "#d7ded9"
  production-placeholder: "#666c68"
typography:
  scale:
    micro: "10px"
    tiny: "12px"
    small: "13px"
    body: "14px"
    medium: "15px"
    modal: "18px"
    display: "20px"
    title: "24px"
    head: "25px"
  display:
    fontFamily: "System UI, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "1.25rem"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "-0.01em"
  body:
    fontFamily: "System UI, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.55
    letterSpacing: "0"
  label:
    fontFamily: "System UI, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "0.6875rem"
    fontWeight: 700
    lineHeight: 1.2
    letterSpacing: "0.1em"
  mono:
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
    fontSize: "0.8125rem"
    fontWeight: 400
    lineHeight: 1.7
    letterSpacing: "0"
rounded:
  none: "0"
  xs: "2px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "16px"
  lg: "24px"
  xl: "32px"
components:
  search-bar:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    borderColor: "{colors.rule}"
    focusBorderColor: "{colors.focus}"
    rounded: "{rounded.none}"
    padding: "14px 16px"
  button:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    borderColor: "{colors.rule}"
    rounded: "{rounded.none}"
    padding: "8px 12px"
  nav-active:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    rounded: "{rounded.none}"
    underlineColor: "{colors.muted}"
---

<!--
THESIS: Staging is one centered bar on a flat field. Navigation and the Table tab are withheld until a lookup; the interface refuses the hero, card stack, and style switcher it replaces.
OWN-WORLD: Near-black ground, grey hairline grid, one-pixel rules, mono figures, cyan only for focus. No bezel, glow, shadow, pill, or rounded surface.
STORY: A visitor pastes an identifier, stay ID, or stint ID. The recorded pathway opens in place; Table stands beside Search as the same lookup's stay and stint log.
FIRST VIEWPORT: A 720px bar centered on the grid. No header, nav, heading, footer, or tabs. Search reveals the top bar, pathway, release disclosure, and actions.
FORM: Flat film ledger, user-committed direction. Seed not used; the brief pinned the world.
-->

# Design System: Bronx

## Overview

**Creative North Star: "Flat Film Ledger."**

Staging strips the lookup to one centered search bar on a ruled near-black field. The first view has no navigation, no heading, no cards, and no decorative chrome. After the first lookup the field becomes a ledger: a quiet top bar, the editable pathway on hairline rules, a collapsed release selector, a compact action row, and a **Table** tab that shows the stays and stints behind the same result.

Production is untouched. It remains the original two-column ruled record workbench in bone paper and vermilion, with Record as its only visual language. The two environments share HTML and text semantics, not a visual identity.

**Key Characteristics:**

- First staging view: one 720px bar, centered, with identifier / `stay_ID` / `stint_ID` as its only visible copy.
- Reveal rule: a lookup, a `?q=` return link, or a prior browser reveal flag shows the top bar, Table tab, dataset disclosure, pathway, and actions.
- One committed staging direction; no style switch and no `?style=` propagation in links or forms.
- Near-black and grey with a sparse hairline grid; cyan appears only on keyboard focus, focus-within, and the data focus marker.
- Mono carries identifiers, dates, facilities, watermarks, and pathway text; system sans carries labels and controls.
- Every staging surface keeps the same rules: 1px borders, zero radius, no shadows, no glows, no bezel.
- Table cells preserve source values and `[MANUAL_EDIT original=…]` tags; a missing book-out stays `UNKNOWN`.

## Colors

### Staging — flat film ledger

- **Ground** (#0b0e10): page field with a 72px hairline grid at low contrast.
- **Panel** (#111619): search bar, inputs, dialog, and non-text controls.
- **Ink** (#dbe3e4): pathway, headings, and primary text.
- **Muted** (#98a4a7): labels, hints, status, inactive nav, and table metadata.
- **Rule** (#2a3235): every divider, border, and table row.
- **Focus** (#7be7ef): keyboard focus, search-bar focus-within, and the focused stay marker only.

### Production — record fallback

Bone (#e8ebe9), paper (#f8f9f7), ink (#171a18), graphite (#4b504d), rule (#c3c8c4), and vermilion (#b23a22) remain the production workbench tokens. Vermilion marks active navigation, provenance, and errors there.

## Typography

**Fonts:** system sans for interface language; `ui-monospace` for data. No display face, no italic tagline on staging, no fluid type.

- **Display:** 600, 1.25rem, 1.2, tracking -0.01em.
- **Body:** 400, 0.875rem, 1.55, 65–75ch measure.
- **Label:** 700, 0.6875rem, uppercase, 0.1em tracking.
- **Mono:** 400, 0.8125rem, 1.7 for identifiers and records.

## Layout

**Entry state.** `body[data-style="film"][data-stage="entry"]` hides the top bar, footer, panel head, notices, release disclosure, pathway, and actions. The shell centers a single `single-panel` block; the form is the only visible content.

**Work state.** Search keeps the same bar at the top of a 108ch column. Order: head, notices, bar, release disclosure, pathway status and textarea, action row. Table replaces the pathway block with stay sections and keeps the same bar above them. Bulk, History, Datasets, Help, and Table render with the top bar visible.

**Reveal rule.** A successful or attempted lookup sets `data-stage="work"` and stores `bronx.staging.revealed.v1` in `localStorage`. A server render with a result or `?q=` starts in work state. A later visit to bare `/` restores work state from the flag; clearing browser data returns to the single bar.

**Responsive.** The bar stacks its button below the input under 620px but stays one outlined control. Tables scroll horizontally. No typography scales with viewport width.

## Components

- **Search bar:** one 1px outline; transparent input; hairline-separated Search button; cyan border only while focus is inside.
- **Buttons:** transparent, square, grey rules; hover brightens text or border, never fills with cyan.
- **Navigation:** flat text, grey inactive, ink active with a 2px grey underline. Table link is hidden in entry state.
- **Status:** plain mono text on the field; no colored rule or alert fill.
- **Pathway:** borderless textarea bounded by two hairlines; no bezel, shadow, or raised sheet.
- **Dataset disclosure:** small summary with a 280px release select; hidden until work state.
- **Table:** hairline rows; stay headings and figures in mono; focused stay gets a cyan top rule and `FOCUS` tag.
- **Dialog:** square panel on a flat dark backdrop; no offset shadow or halo.

## Motion

No entrance choreography. Only state feedback: focus border and standard control hover changes within 150ms. Lookup updates in place through `POST /api/lookup`; no page-load sequence.

## Do's and Don'ts

- Keep existing UI copy, rendered pathway text, watermarks, and source JSON byte-identical.
- Keep manual-entry fields limited to the nine web-editable fields; `location` and `landmark` stay unavailable.
- Keep Datasets, Bulk, History, Help, demo, copy, export, and update check reachable after reveal.
- Don't add cards, gradients as decoration, glows, bezel frames, icons, rounded pills, or decorative motion.
- Don't use cyan for links, active navigation, errors, or buttons at rest.
- Don't present a missing book-out as current detention; show `UNKNOWN`.
- Don't change production styling or remove the Record workbench fallback.
