---
name: Bronx — ICE Detention Pathway
description: A compact desktop workbench for reconstructing recorded ICE detention pathways.
colors:
  bone: "#e8ebe9"
  paper: "#f8f9f7"
  ink: "#171a18"
  graphite: "#4b504d"
  rule: "#c3c8c4"
  vermilion: "#b23a22"
  vermilion-deep: "#8e2d19"
typography:
  display:
    fontFamily: "System UI, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "1.25rem"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "-0.02em"
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
    letterSpacing: "0.09em"
  mono:
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
    fontSize: "0.8125rem"
    fontWeight: 400
    lineHeight: 1.6
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
  button-primary:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.paper}"
    rounded: "{rounded.xs}"
    padding: "8px 14px"
  button-secondary:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.xs}"
    padding: "8px 12px"
  input:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.xs}"
    padding: "8px 10px"
  nav-active:
    backgroundColor: "{colors.bone}"
    textColor: "{colors.vermilion}"
    rounded: "{rounded.none}"
    padding: "4px 0"
---

<!--
THESIS: Staging is a dark evidence workspace, not a light form dressed up at night. It refuses the calm cream document and the generic neon terminal.
OWN-WORLD: Graphite steel, amber inventory tags, deep green with one lit ivory sheet, and near-black film with cyan frame marks. Hairline seams, stencil labels, mono figures.
STORY: A researcher enters an identifier, sees the recorded pathway, and opens the stays/stints table to check each placement without changing the text.
FIRST VIEWPORT: Top strip, style switch in the head, identifier rail, status, then either the editable pathway or the tabular stay log. Table tab sits beside Search.
FORM: Chain-of-custody manifest wall, position 7 of the grounded list, seed 4c957886. Reading room and microfilm are the kept challengers.
-->

# Design System: Bronx

## Overview

**Creative North Star: "Central Rail Record."**

A desktop-first lookup surface built from rules, columns, and typographic hierarchy. Production is the original ruled record sheet: a narrow identifier rail on the left and a paragraph-width pathway field on the right. Staging is a dark evidence workspace with one lookup panel, three style studies, and a Table tab that shows the recorded stays and stints behind the same pathway text. No style changes data, copy, history, or cache behavior.

Record keeps bone paper and vermilion provenance marks. The three dark studies use material from research rooms: a chain-of-custody manifest wall, a night reading lamp, and a microfilm frame index. Color marks state and provenance, not decoration. The interface stays quiet while the pathway text carries the weight of the work.

**Key Characteristics:**

- Two-column workbench on production; one-panel lookup and Table tab on staging.
- Four staging styles share one lookup path: Record fallback plus Manifest, Reading room, and Microfilm.
- Hairline rules and typographic weight replace panels, shadows, and badges in production and Record.
- Core fields keep one reading order; dark styles change framing, density, and placement.
- Controls stay square and compact; mono carries identifiers, dates, facilities, and pathway text.
- Table cells show source values with `[MANUAL_EDIT original=…]` still visible; missing book-out stays `UNKNOWN`.

## Colors

Bone ground, paper fields, iron-black type, and a single vermilion provenance mark.

### Primary

- **Vermilion** (#b23a22): Active nav item, watermark marker, and error state only. Never used for decoration or large fills.

### Neutral

- **Bone** (#f3efe6): Page ground and rail ground.
- **Paper** (#fbf9f3): Inputs, textarea, dialog, and raised reading surface.
- **Iron Black** (#171714): Headings, primary buttons, and body text.
- **Graphite** (#4d4b44): Secondary text, labels, and table metadata.
- **Rule** (#c9c2b4): Hairline borders, dividers, and table rows.

### Named Rules

**The Vermilion Rule.** Vermilion marks where the user is or where the data came from. If it is not active state or provenance, it does not get vermilion.

**The No-Card Rule.** In production and Record, groups are made with rules, spacing, and alignment. No rounded card stacks, gradients, or drop shadows. Manifest uses steel drawers, Reading room uses one lit sheet, Microfilm uses a framed viewer; none uses generic floating cards.

## Typography

**Display Font:** System UI
**Body Font:** System UI
**Mono Font:** ui-monospace

One family carries navigation, labels, and headings; monospace carries identifiers and pathway text. Character: clerical, precise, unshowy. Weight and rule position do the hierarchy work.

### Hierarchy

- **Display** (600, 1.25rem, 1.2): Page title in the record head.
- **Title** (600, 1rem, 1.3): Section headings and dialog title.
- **Label** (700, 0.6875rem, 1.2, uppercase): Field labels and table headers.
- **Body** (400, 0.875rem, 1.55): Explanatory copy and table cells; 65–75ch measure.
- **Mono** (400, 0.8125rem, 1.6): Identifier input, result textarea, watermarks, and code.

## Layout

- Top bar: 52px fixed strip with brand, version, and navigation. Staging adds **Table** beside **Search**; **Datasets** stays visible in both environments.
- Production desktop main: `minmax(280px, 320px)` query rail + `minmax(0, 1fr)` result field, separated by a vertical hairline.
- Production query rail: identifier, release, search action, secondary actions, status, recent lookups. Sticky below the top bar.
- Staging Search: one panel. Identifier and Search sit above status and editable pathway textarea. Release moves into a collapsed **Dataset** disclosure; other actions stay as a compact row under the output.
- Staging Table: one panel. Identifier and Search sit above a stay-by-stay table; each stay carries arrest, entry, release, and summary facts, then one row per stint with stint ID, book-in, facility, and book-out. Arrests without a stay get their own table.
- Staging search stays in place through `POST /api/lookup`. Repeated queries reuse a session cache keyed by dataset build, release, identifier, and manual overrides. A sticky output head keeps **Copy** reachable while reading. Production keeps full-page search.
- Other pages: single record column with the same head, section rules, and table language.
- Small screens: one column; rails lose sticky position and vertical dividers.

## Staging style studies

Switch with the **Staging style** control or `?style=record|manifest|reading|film`. Legacy `console` maps to Manifest and `dossier` maps to Reading room. The change is visual only: same lookup path, same cache key, same JSON response, same pathway text.

- **Record** — fallback bone ground, paper field, vermilion provenance mark. Head, form, dataset disclosure, status, output, then actions. The baseline ruled sheet.
- **Manifest** — lead dark direction. Graphite-steel ground, amber inventory tags, stencil labels, hairline seams. Intake is a drawer rail; output and table are logged sheets. State reads like a chain-of-custody manifest.
- **Reading room** — deep green-black ground with one warm ivory illuminated sheet, brass rules, and a restrained serif display. The pathway table sits in the lit document margin; the rest of the page stays dark.
- **Microfilm** — near-black viewer bezel, cool grey seams, cyan frame marks, mono figures. The output reads as a frame and the table as a frame index, with `FRAME` and `SYS` markers only as framing.

The sticky output head and auto-copy stay in all four. Record uses light color scheme; the three dark studies use dark.

## Elevation & Depth

Production and Record stay flat; depth comes from paper-on-bone contrast and 1px rules. Manifest uses a single offset shadow on steel panels, Reading room uses one soft lamp shadow on the lit sheet, and Microfilm stays framed by seams only. Dialogs use a single hard shadow with offset; no ambient color halos.

## Shapes

- Radius: 2px maximum on controls and dialogs; 0 on table cells, rails, and rules.
- Borders: 1px solid rule; no double borders, no colored focus glows.
- Focus: 2px foreground outline with 2px offset; amber in Manifest, brass in Reading room, cyan in Microfilm.

## Components

- **Primary button:** Iron-black fill, paper text, square corners, compact padding.
- **Secondary button:** Paper fill, iron-black text, rule border; hover darkens text and border only.
- **Input / select / textarea:** Paper fill, rule border, mono for identifier and pathway, system sans elsewhere.
- **Status line:** Thin rule above and below; graphite text, vermilion left mark on error.
- **Navigation:** Flat text links; active page marked vermilion with a 2px underline, no pill.
- **Table:** Hairline row rules, uppercase label headers, no zebra fill; mono figures. Stays table carries one row per stint and keeps `[MANUAL_EDIT original=…]` visible in edited facts.
- **Dialog:** Paper sheet with hard offset shadow; field groups separated by top rules.

## Do's and Don'ts

- Keep existing app strings unchanged; new Table labels only name fields already in the pathway data.
- Keep controls in the same order; redesign placement and density only.
- Keep identifiers, dates, facilities, and pathway text monospaced.
- Use rules and whitespace to separate groups.
- Don't add cards, gradients, hero art, icons, marketing copy, rounded pills, or decorative motion. The dark studies use only the framing described above.
- Don't recolor success states green; a plain ruled status is enough.
- Don't present a missing book-out as current detention; show `UNKNOWN`.
- Don't hide the dataset watermark or browser-local history note.
