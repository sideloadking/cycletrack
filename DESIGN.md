---
name: Cycling Progress Tracker
description: A private, local cycling analytics app — honest power estimates with bands, elevation rebuilt from UK lidar, rendered as roadside signs.
colors:
  ground: "#eef0ec"
  structure: "#232a31"
  structure-2: "#2b343d"
  structure-line: "#37424d"
  plate: "#ffffff"
  plate-2: "#f5f7f2"
  plate-3: "#ecefe7"
  line: "#d8dcd2"
  line-soft: "#e6e9e0"
  ink: "#191d22"
  muted: "#5b646e"
  faint: "#8a939c"
  green: "#0e7a44"
  green-deep: "#0a5c33"
  green-ink: "#ffffff"
  blue: "#1b5c9e"
  red: "#c9342b"
  amber: "#e8a400"
  amber-ink: "#2e2200"
  amber-deep: "#7a5600"
typography:
  display:
    fontFamily: "Archivo, system-ui, sans-serif"
    fontSize: "26px"
    fontWeight: 700
    letterSpacing: "0.1px"
  headline:
    fontFamily: "Archivo, system-ui, sans-serif"
    fontSize: "15px"
    fontWeight: 700
    letterSpacing: "0.2px"
  body:
    fontFamily: "Archivo, system-ui, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.55
  data:
    fontFamily: "Archivo, system-ui, sans-serif"
    fontSize: "30px"
    fontWeight: 700
    fontFeature: "tnum"
  label:
    fontFamily: "'IBM Plex Mono', ui-monospace, monospace"
    fontSize: "11px"
    fontWeight: 600
    letterSpacing: "1.2px"
  measurement:
    fontFamily: "'IBM Plex Mono', ui-monospace, monospace"
    fontSize: "12px"
    fontWeight: 400
rounded:
  sm: "6px"
  md: "10px"
spacing:
  xs: "8px"
  sm: "12px"
  md: "14px"
  lg: "20px"
  xl: "24px"
components:
  button-primary:
    backgroundColor: "{colors.green}"
    textColor: "{colors.green-ink}"
    rounded: "{rounded.sm}"
    padding: "9px 15px"
  button-default:
    backgroundColor: "{colors.plate}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    padding: "9px 15px"
  button-danger:
    backgroundColor: "{colors.plate}"
    textColor: "{colors.red}"
    rounded: "{rounded.sm}"
    padding: "9px 15px"
  input:
    backgroundColor: "{colors.plate}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    padding: "8px 10px"
  nav-link:
    textColor: "#b7bfc8"
    rounded: "{rounded.sm}"
    padding: "9px 12px"
  nav-link-active:
    backgroundColor: "{colors.green}"
    textColor: "{colors.green-ink}"
    rounded: "{rounded.sm}"
    padding: "9px 12px"
  card:
    backgroundColor: "{colors.plate}"
    rounded: "{rounded.md}"
    padding: "18px 20px"
---

# Design System: Cycling Progress Tracker

## Overview

**Creative North Star: "The Roadside Signboard"**

The analysis reads like the road it came from. Confident data are permanent signs — crisp ink on sign-white, mounted on dark gantry steel. Estimates are temporary works: amber plates, and when the range is wide, drawn with diagonal hatching like a provisional road marking. Repeated routes earn route numbers. The product's honesty promise is carried by the material itself: a sign that is amber or hatched announces its own uncertainty; nothing is faked into permanence.

The world is deliberately light — the daylight reference system of British roads — a decisive break from the previous night-instrument panel. Depth comes from the contrast between dark sign structure and bright sign faces, never from floating shadows. Cards are flat plates with hairline borders; the one floating element (the toast) is the only real shadow in the system.

**Key Characteristics:**
- Confident = permanent green sign; estimated = temporary amber; wide = hatched.
- Flat white sign plates on a pale verge ground; dark steel gantry frame.
- One ink; hierarchy by type size and weight, not color.
- Transport-family letterforms (Archivo) with bold tabular numerals.
- Honest empty states and plain-language copy throughout.

## Colors

The palette is the British road-sign system: one ink, one confident green, one motorway blue, one road red, one temporary-works amber, on sign-white over a pale verge.

### Primary
- **Primary Route Green** (#0e7a44): confident data, active navigation, primary buttons, positive signals, the LOCAL ONLY plate. White text on it at all times.
- **Route Green Deep** (#0a5c33): hover/pressed states of the green.

### Secondary
- **Motorway Blue** (#1b5c9e): cold data series (speed, CTL/fitness), links in map attribution.

### Tertiary
- **Road Red** (#c9342b): danger only — delete actions, errors, finish markers. Never decorative.
- **Temporary Works Amber** (#e8a400): estimates and context. Amber plates carry dark amber ink (#2e2200); as text on white use **Amber Deep** (#7a5600). Wide estimates render as the diagonal hatch.

### Neutral
- **Sign White** (#ffffff): plates, cards, inputs, sign faces.
- **Verge Green-Grey** (#eef0ec): page ground.
- **Gantry Steel** (#232a31): sidebar frame, with **Steel Hover** (#2b343d) and hairlines (#37424d). Sidebar text is white and muted steel greys.
- **Sign Black** (#191d22): primary text, numerals.
- **Road Grey** (#5b646e): secondary text, labels on white.
- **Hairline** (#d8dcd2): 1px borders; **Faint Hairline** (#e6e9e0): chart grids and row separators.

### Named Rules

**The Permanent/Temporary Rule.** A number is either a permanent sign (green, solid, crisp) or a temporary works (amber; hatched when wide). If a value is estimated, it must never wear the colors of certainty. Red is reserved for danger and is not part of the confidence scale.

**The No-Fake-Tone Rule.** Uncertainty is drawn as ordered diagonal hatching — never as a smooth gradient pretending precision. A hatched band is a sign still under construction.

## Typography

**Display Font:** Archivo (700–800), self-hosted woff2.
**Body Font:** Archivo (400–500).
**Label/Measurement Font:** IBM Plex Mono (400–600) for labels, units, chips, and measurement notes.

**Character:** A workhorse Transport-family grotesk carrying every word and numeral, like the road itself: sturdy, upright, no styling tricks. Hierarchy comes from size and weight in one ink — the way a direction sign tells you which destination matters.

### Hierarchy
- **Display** (700, 26px): page titles (topbar).
- **Headline** (700, 15px): card titles.
- **Body** (400, 14px/1.55): table cells, prose, buttons.
- **Data** (700, 24–30px, tabular): hero numerals and instrument values. The numerals are the sign's content.
- **Label** (600, 11px, uppercase, +1.2px tracking, mono): field labels, chips, table headers, readout labels.
- **Measurement** (400, 12px, mono): band notes, range text, sidebar status.

### Named Rules

**The Type Floor Rule.** No functional text below 11px; body copy never below 14px. The old micro-type system (9.5–10.5px labels) is gone. Letterspacing and weight carry the instrument feel, not size.

**The Size-Only Hierarchy Rule.** Emphasis is weight and size in one ink. Colored or gradient headlines are not used.

## Layout

Fixed 218px gantry sidebar on the left (dark steel) with the content on the right (max-width 1400px, 24–30px padding). The layout shell is a standard Operate topology: persistent nav, page title + status plate in the topbar, content plates stacked with a 14px rhythm. Cards sit on a 12-column-friendly grid with 4/3/2 column breakpoints (2-column below 1100px, 1-column below 620px). The ride readout strip is a direction sign with seven destinations (7 columns; 4 below 1100px; 2 below 620px). More space above headings than below; 8px is the smallest gap in the system.

## Elevation & Depth

The system is flat by construction. Depth is carried by the material contrast between dark gantry steel (sidebar, structure) and bright sign faces — never by shadows on plates. Cards, instruments, and inputs are flat white plates with 1px hairlines. The only shadow in the system is the toast, which floats over the road with a tight offset shadow (`0 4px 14px rgba(35,42,49,.2)`).

### Named Rules

**The No-Floaty-Shadow Rule.** Plates never carry blurred drop shadows. A 1px hairline and the steel-versus-white contrast is all the depth the world needs. The floaty 30px card shadows of the previous design are banned.

## Shapes

Sign plates are gently rounded (10px cards, 6px buttons/inputs/chips). Corners stay soft but not pill-shaped; chips use 4px. Hairlines are 1px, dashed where the road is provisional (dropzone, wide bands). The dropzone's dashed border is a road marking waiting for work. Leaflet map controls and attribution are restyled as white plates on the light CARTO tile set.

## Components

### Buttons
- **Shape:** 6px radius, 1px hairline border, 9px 15px padding; min-height 44px on touch screens.
- **Primary:** green plate, white text, 600 weight; hover deepens to #0a5c33 with a tight 3px shadow.
- **Default:** white plate, ink text; hover borders green and tints the ground.
- **Danger:** ink text that turns road red on hover — never a filled red plate.

### Chips (Pills)
- **Style:** 4px radius, 11px uppercase mono with letter-spacing, a 6px dot prefix.
- **High/Confident:** white on primary green.
- **Med/Context:** dark amber ink on amber.
- **Low:** road grey on inset white with a hairline.
- The dot is hollow for context, solid for confident.

### Cards / Plates
- **Corner Style:** 10px.
- **Background:** sign white with a 1px hairline border; inset face #f5f7f2 for hover rows.
- **Shadow Strategy:** none (see Elevation).
- **Internal Padding:** 18px 20px.

### Inputs / Fields
- **Style:** white plate, 1px hairline, 6px radius, 13px Archivo.
- **Focus:** 2px green border with a 3px soft green ring; invalid fields turn road red.
- **Labels:** 11px uppercase mono above the field.

### Instrument (Direction Sign)
The signature component: one white plate divided into fields by hairline rules, like destinations on a large direction sign. Each field carries an 11px uppercase label and a 24–30px Archivo numeral. Estimate fields append a small amber ESTIMATED tag and an honest range band below the numeral — a green bar when tight, a diagonal-hatch bar when wide, with the range and tag printed beneath.

### Navigation
- **Style:** flat list on gantry steel; 13.5px Archivo, muted steel grey.
- **Active:** the item becomes a filled green sign with white text — no underline, no left bar.
- **Mobile:** the sidebar folds into a top steel bar with horizontally scrolling sign links (44px touch targets).

### Map & Replay
- **Map:** light CARTO tiles, white-plate zoom controls and attribution, green route polyline, green start marker, road-red finish marker, white-ringed green playhead.
- **Scrubber:** green track with a white-ringed green thumb; the readout strip below is a seven-destination direction sign.
- **Play/Pause:** a drawn SVG triangle/bars icon — never a Unicode glyph.

## Do's and Don'ts

### Do:
- **Do** render confident data as green and solid, estimates as amber, and wide ranges as diagonal hatching.
- **Do** keep plates flat with 1px hairlines and let steel-versus-white carry depth.
- **Do** set functional text at or above 11px and body copy at 14px.
- **Do** use Archivo for display/body/data numerals and IBM Plex Mono for labels and measurements.
- **Do** carry hierarchy with size and weight in one ink.

### Don't:
- **Don't** use blurred drop shadows on cards or the old headlight-amber glow.
- **Don't** use smooth gradients to represent uncertainty — hatch it.
- **Don't** use colored border-left bars for active states; fill the sign instead.
- **Don't** put functional text below 11px.
- **Don't** use Unicode glyphs or emoji as icons — draw SVGs in the sign's own weight.
- **Don't** use dark map tiles; the world is daylight.
