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

# Design System: Bronx

## Overview

**Creative North Star: "Central Rail Record."**

A desktop-first lookup surface built from rules, columns, and typographic hierarchy rather than cards. Production is a ruled record sheet with a narrow identifier rail on the left and a paragraph-width pathway field on the right. Staging reduces that to one ruled panel: identifier in, pathway out. Nothing floats, nothing glows, nothing announces itself as a marketing surface.

Color is almost entirely ink on bone paper. Vermilion appears only on the active navigation state and provenance marks — the dataset watermark and the current record — so it reads as a filing mark rather than decoration. The interface stays quiet while the pathway text carries the weight of the work.

**Key Characteristics:**

- Two-column workbench on production; one-panel lookup on staging.
- Hairline rules and typographic weight replace panels, shadows, and badges.
- Identifier, status, and watermark are always in the same place.
- Controls are square, compact, and system-font native.
- Pathway output is monospaced, editable, and measured for reading.

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

**The No-Card Rule.** Groups are made with rules, spacing, and alignment. No rounded card stacks, gradients, or drop shadows.

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

- Top bar: 52px fixed strip with brand, version, and five-item navigation; **Datasets** stays visible in both environments.
- Production desktop main: `minmax(280px, 320px)` query rail + `minmax(0, 1fr)` result field, separated by a vertical hairline.
- Production query rail: identifier, release, search action, secondary actions, status, recent lookups. Sticky below the top bar.
- Staging main: one `84ch` ruled panel. Identifier and Search sit above the status and editable pathway textarea. Release moves into a collapsed **Dataset** disclosure; other actions stay as a compact row under the output.
- Staging search: the form posts to `POST /api/lookup`, then updates status, pathway, history, and form state in place. Repeated queries reuse a session cache keyed by dataset build, release, identifier, and manual overrides. A sticky output head keeps **Copy** reachable while reading. Production keeps full-page search.
- Other pages: single ruled record column with the same head, section rules, and table language.
- Small screens: one column; rail loses sticky position and vertical divider.

## Elevation & Depth

Flat. Depth comes from paper-on-bone contrast and 1px rules. Dialogs use a single hard shadow with offset; no ambient color halos.

## Shapes

- Radius: 2px maximum on controls and dialogs; 0 on table cells, rails, and rules.
- Borders: 1px solid rule; no double borders, no colored focus glows.
- Focus: 2px iron-black outline with 2px offset.

## Components

- **Primary button:** Iron-black fill, paper text, square corners, compact padding.
- **Secondary button:** Paper fill, iron-black text, rule border; hover darkens text and border only.
- **Input / select / textarea:** Paper fill, rule border, mono for identifier and pathway, system sans elsewhere.
- **Status line:** Thin rule above and below; graphite text, vermilion left mark on error.
- **Navigation:** Flat text links; active page marked vermilion with a 2px underline, no pill.
- **Table:** Hairline row rules, uppercase label headers, no zebra fill; hover underlines the identifier.
- **Dialog:** Paper sheet with hard offset shadow; field groups separated by top rules.

## Do's and Don'ts

- Keep every visible string from the existing app unchanged.
- Keep controls in the same order; redesign placement and density only.
- Keep identifiers and pathway text monospaced.
- Use rules and whitespace to separate groups.
- Don't add cards, gradients, hero art, icons, marketing copy, rounded pills, or decorative motion.
- Don't recolor success states green; a plain ruled status is enough.
- Don't hide the dataset watermark or browser-local history note.
