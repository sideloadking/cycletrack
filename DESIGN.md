---
name: VeloTrack Route Atlas
description: A private cycling performance studio rendered as a daylight route atlas — calm surfaces, measured type, and honest data materiality.
colors:
  # ground & surfaces
  canvas: "#f4f7f4"
  canvas-deep: "#eaf1ec"
  surface: "#ffffff"
  surface-soft: "#f0f5f1"
  surface-tint: "#e7f3eb"
  white: "#ffffff"
  # ink ramp (all pass WCAG AA on the surfaces they sit on)
  ink: "#17211d"
  ink-soft: "#34423a"
  muted: "#55655c"
  subtle: "#5c6b63"
  # hairlines
  line: "#dfe7e1"
  line-strong: "#cad7ce"
  # graph frame
  graph-grid: "#edf1ee"
  graph-grid-strong: "#d3ddd6"
  graph-cursor: "#123a28"
  # green — confident, positive, navigational
  green: "#1b6f4d"
  green-deep: "#11553a"
  green-soft: "#dff3e7"
  green-line: "#a9cdb4"
  green-line-soft: "#d3e7d9"
  green-hover: "#d9f0e1"
  # blue — cool contextual series
  blue: "#2f67d7"
  blue-deep: "#2453b4"
  blue-soft: "#e7efff"
  # orange — estimate / interpret with care
  orange: "#bd6a1d"
  orange-deep: "#8f4f11"
  orange-soft: "#fff0dc"
  # purple — secondary analytical emphasis
  purple: "#6558bf"
  purple-deep: "#574aa9"
  purple-soft: "#eeecff"
  # red — destructive actions and real errors
  red: "#b44c4b"
  red-deep: "#7f3332"
  red-line: "#e7c3c3"
  red-line-strong: "#d68d8b"
  red-soft: "#ffebeb"
  # canvas atmosphere
  canvas-glow: "rgba(212,239,222,.62)"
  # map overlays
  map-badge-border: "rgba(213,226,216,.95)"
  slider-thumb-shadow: "rgba(25,70,45,.26)"
  # skeleton shimmer
  skeleton-shimmer: "#ecf1ed"
  # tooltip ink plate
  tooltip-ink: "rgba(23,33,29,.97)"
  tooltip-body: "rgba(242,247,243,.82)"
  tooltip-shadow: "rgba(16,30,22,.3)"
  # translucent plates & shadows (alpha varies per use)
  plate-glass: "rgba(255,255,255,.94)"
  shadow-tint: "rgba(28,54,39,.08)"
typography:
  display:
    fontFamily: "Archivo"
    fontSize: "52px"
    fontWeight: 800
    letterSpacing: "-0.04em"
  body:
    fontFamily: "IBM Plex Sans"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.55
  data:
    fontFamily: "IBM Plex Mono"
    fontSize: "12px"
    fontWeight: 500
    fontFeature: "tnum"
  scale:
    brand-foot: "10px"
    caption: "11px"
    small: "12px"
    base: "13px"
    body: "14px"
    lead: "15px"
    mobile-title: "16px"
    section: "17px"
    card-title: "18px"
    feature-value: "19px"
    panel-title: "20px"
    dropzone-title: "23px"
    drift-value: "23px"
    metric-value: "25px"
    record-value: "26px"
    detail-title-min: "29px"
    insight-value: "30px"
    display-min: "32px"
    mobile-h1: "34px"
    feature-insight: "36px"
    mobile-h1-460: "38px"
    detail-title: "45px"
    display: "52px"
rounded:
  micro: "2px"
  dot: "3px"
  swatch: "4px"
  leaflet: "6px"
  control: "7px"
  sm: "8px"
  segmented: "9px"
  icon: "10px"
  brand: "11px"
  md: "12px"
  badge: "13px"
  mark: "14px"
  lg: "18px"
  xl: "26px"
  pill: "999px"
---

# Design System: VeloTrack Route Atlas

## North star

VeloTrack is a **private cycling performance studio**, not a social feed and not an on-bike computer. The interface should feel like a daylight route atlas: quiet enough to study, specific enough to trust, and alive only where movement helps explain a Ride.

The memorable signature is the **route ribbon**. It is a thin, hand-drawn route trace that appears in the overview hero, Ride library feature, Route cards, and Route comparison. It turns the product's subject into a visual language without turning the dashboard into a map wallpaper.

Clear hierarchy, generous touch targets, responsive surfaces, and material roles make the interface easy to learn. It does not mean copying another product's colors, card templates, or chrome.

## Material roles

- **Canvas** (`#f4f7f4`) is the quiet page ground.
- **Surface** (`#ffffff`) is a readable plate for a decision or a group of facts.
- **Surface soft** (`#f0f5f1`) is used for controls, secondary panels, and local/private notes.
- **Green** (`#1b6f4d`) means a confident, positive, or navigational state. Green is never used to hide uncertainty.
- **Blue** (`#2f67d7`) is a cool contextual series: fitness, speed, or normalized power.
- **Orange** (`#bd6a1d`) means an estimate or a warning about interpretation. Wide estimates use ordered diagonal hatching.
- **Purple** (`#6558bf`) is reserved for secondary analytical emphasis, not status.
- **Red** (`#b44c4b`) is reserved for destructive actions and actual errors.

### Honest data rule

Every estimated value keeps its estimate tag and, when available, its band. A power estimate is never styled like a measured sensor value. A wide band is hatched; smooth gradients are not used to suggest precision.

## Typography

- **Archivo 700–800** is the display face. It is compact, sturdy, and used for page titles, Ride names, and metric values.
- **IBM Plex Sans 400–600** is the body face. It carries explanations and controls with a neutral, readable rhythm.
- **IBM Plex Mono 500–700** is the utility face. It carries dates, units, labels, status tags, and table values so data columns align.

Functional text never drops below 11px for utility labels or 12px for supporting copy; the quietest brand marking (the sidebar footer) may sit at 10px. Body copy targets 13–15px. Large values use tabular numerals and tight letter spacing. Every label tone passes WCAG AA on the surface it sits on.

## Layout

The desktop shell is a 252px persistent studio rail and a fluid content column capped at 1480px. The rail is white and quiet; the product does not need a dark frame to feel authoritative. On small screens, the rail becomes a 61px sticky header with a single high-value Import action.

Pages use this rhythm:

1. **Page head** — eyebrow, plain-language thesis, short explanation, one primary action.
2. **Hero or lead plate** — the most characteristic insight for that page.
3. **Evidence** — charts, tables, or replay surfaces with enough context to interpret them.
4. **Next action** — open a Ride, compare a Route, import history, or tune the profile.

The grid uses 20px between plates, 24px internal padding, and 1px hairlines. Cards use 8–18px radii with 26px for the hero; micro shapes (graph bars, swatches, marker dots, controls) use the 2–7px steps. There are no decorative numbered markers unless the number is a real sequence, such as the import process.

## Signature surfaces

### Route ribbon

A route ribbon is a single SVG trace with a soft under-stroke, a measured green line, and two small location nodes. It is a subject-specific signature, not a generic chart. It should remain sparse and never compete with a graph that carries real data.

### Instrument metric

Metric plates use a small mono label, a strong Archivo value, and an optional unit. Estimated power adds an orange tag and an uncertainty track. A confidence signal is written in words as well as color.

### Graph frame

The graph grammar is a calm instrument, in the spirit of a modern data tool: the frame disappears and the data reads at a glance. Every graph is a small purpose-built SVG scene with one shared grammar across Overview, Route, and Ride pages.

- **No chart box.** There are no axis lines, no tick marks, and no vertical gridlines. The only ruling is a faint 1px horizontal hairline at each y-tick; a slightly stronger hairline marks zero on either axis.
- **Type.** Axis values are IBM Plex Sans 500 at 11px in muted gray with tabular numerals. Axis titles are the utility face: IBM Plex Mono 600 at 11px, uppercase, tracked. Legends are HTML chips — a rounded color square plus a 12px sans label — right-aligned above the plot, shown only when a chart carries two or more series.
- **Lines.** Data lines are 1.7–2.6px with rounded joins and caps. Sparse series (a few hundred points or fewer) are drawn as gentle monotone curves; dense telemetry stays straight. Points appear on sparse series with a white ring; hovering or seeking enlarges the point at the cursor.
- **Bars.** Bars have a rounded top, sit at roughly 60% opacity, and rise from their baseline. Bars may carry per-point semantic color.
- **Uncertainty.** The power band is a soft filled envelope with an ordered diagonal hatch, beneath the estimate line. Bands keep their hatching — a smooth gradient is never used to disguise a wide estimate.
- **Y-domains.** Charts whose series carry wide bands use robust quantile domains (2–98%) so a genuinely uncertain band stays visible without crushing the signal to the bottom of the plot. The tooltip still reports the true band.
- **Tooltip.** Hover opens a dark ink card with a small uppercase head line, then one row per series — a color dot, label, and bold tabular value — and a band line in orange-soft where the estimate carries one. It follows the cursor with clamping and flips to stay inside the plate.
- **Cursor & hover.** The replay cursor is a solid 1.5px ink line with white-ringed dots on every series. Hover uses a dashed muted hairline instead, so seek and inspect stay visually distinct.
- **Entrance.** A single ~500ms moment: the plot fades and rises slightly, lines draw in from left to right, bars rise from their baseline. It replays when a filter or picker redraws a chart, which makes control changes feel deliberate. Disabled under `prefers-reduced-motion`.
- A graph title explains the question it answers, not the implementation behind it.

## Graph and replay rules

- Render a graph once and update only the SVG scene needed for interaction; do not recreate a charting library instance for a control change.
- Keep the Ride timeline normalized so distance, elapsed time, HR, power, map position, and the readout share one cursor.
- Replay movement may run at full visual speed, but graph cursor updates are throttled to a calm 30fps budget and move a single lightweight SVG cursor line.
- The power band is a filled, hatched envelope beneath the estimate line.
- Fixed-HR trend lines use green for the primary signal and orange markers for context or uncertainty.
- Blue is used for a contextual comparison, never as a second confidence scale.
- Hover text uses units and plain language: `214 W`, `band 168–274 W`, `context`.
- Empty graphs explain what data is missing and how to create it; they never show a blank rectangle.

## Motion

The page enters with a short, low-distance rise. Cards do not float or bounce. Hover lifts are limited to clickable Route cards and primary actions. Graphs enter with one calm draw-in moment (see Graph frame). Ride replay is the one sustained animation: the map marker, readout, and chart cursors move together because the motion is the explanation.

`prefers-reduced-motion` disables page movement, shimmer, hover transforms, and replay animation transitions. Keyboard focus uses a visible blue ring with a 2px offset. All clickable rows have a keyboard equivalent.

## Copy

Write from the rider's side of the screen. Use "Import rides", "Open latest ride", "Save changes", and "Play replay". Prefer "estimated power" and "same roads" over internal terms. Empty states point to the next useful action. Errors state what failed without vague apologies.

The product's recurring vocabulary is **Ride**, **Route**, **watts at the same heart rate**, **power estimate**, **uncertainty band**, **cardiac drift**, **local**, and **private**.

## Do / don't

### Do

- Use daylight surfaces and green as the product anchor.
- Let the route ribbon provide the distinctive visual memory.
- Keep measured facts, contextual facts, and estimates visibly different.
- Use one graph grammar across Overview, Route, and Ride pages.
- Prefer a deep readable plate over a collection of small decorative widgets.
- Keep every primary interaction usable at touch size and with a keyboard.

### Don't

- Do not use dark glass, neon glows, or gradients as the default material.
- Do not use a color alias that makes orange estimates look confident.
- Do not use a smooth fill to disguise a wide power band.
- Do not use a chart title that only names a metric without stating its question.
- Do not hide local/private status in a settings page.
- Do not claim measured power, FTP, or TSS where the product only has an estimate.
