---
name: DeckLens
description: A broadcast of your own Clash Royale match, on the second monitor.
colors:
  ground: "#eef0f5"
  band: "#ffffff"
  band-2: "#e4e8f0"
  rule: "#cfd5e2"
  edge: "#747e96"
  text: "#0c1222"
  muted: "#485270"
  team-blue: "#1d4ed8"
  team-blue-wash: "#dfe7fb"
  team-red: "#c21f2c"
  team-red-ink: "#b81d29"
  team-red-wash: "#f8e0e2"
  live: "#f5c400"
  on-live: "#0c1222"
  elixir: "#8f2bbd"
  ok: "#0f7a43"
  warn: "#8a5a00"
  bad: "#b42318"
  ground-dark: "#0a0e18"
  band-dark: "#111727"
  band-2-dark: "#19213a"
  rule-dark: "#263050"
  edge-dark: "#6c789c"
  text-dark: "#f1f4fb"
  muted-dark: "#a7b1cc"
  team-blue-dark: "#2f62e8"
  team-blue-ink-dark: "#7aa2ff"
  team-red-dark: "#d62f3b"
  team-red-ink-dark: "#ff7a82"
  live-dark: "#ffd23f"
  elixir-dark: "#9b2fc9"
typography:
  display:
    fontFamily: "Barlow Condensed, Bahnschrift, Arial Narrow, sans-serif"
    fontSize: "clamp(1.75rem, 3.2vw, 3rem)"
    fontWeight: 700
    lineHeight: 1
    letterSpacing: "0.01em"
  score:
    fontFamily: "Barlow Condensed, Bahnschrift, Arial Narrow, sans-serif"
    fontSize: "clamp(3rem, 5vw, 4.5rem)"
    fontWeight: 700
    lineHeight: 0.9
  headline:
    fontFamily: "Barlow Condensed, Bahnschrift, Arial Narrow, sans-serif"
    fontSize: "2rem"
    fontWeight: 700
    lineHeight: 1.05
    letterSpacing: "0.02em"
  title:
    fontFamily: "Barlow Condensed, Bahnschrift, Arial Narrow, sans-serif"
    fontSize: "1.125rem"
    fontWeight: 700
    lineHeight: 1.1
    letterSpacing: "0.04em"
  body:
    fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, sans-serif"
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: "Barlow Condensed, Bahnschrift, Arial Narrow, sans-serif"
    fontSize: "0.9375rem"
    fontWeight: 700
    lineHeight: 1
    letterSpacing: "0.05em"
  fact:
    fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.5
  caption:
    fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 400
    lineHeight: 1.4
rounded:
  none: "0px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "20px"
  gutter: "clamp(16px, 2.4vw, 40px)"
  page: "1760px"
components:
  button-primary:
    backgroundColor: "{colors.team-blue}"
    textColor: "#ffffff"
    typography: "{typography.title}"
    rounded: "{rounded.none}"
    padding: "8px 18px"
  button-primary-them:
    backgroundColor: "{colors.team-red}"
    textColor: "#ffffff"
    typography: "{typography.title}"
    rounded: "{rounded.none}"
    padding: "8px 18px"
  button-quiet:
    backgroundColor: "transparent"
    textColor: "{colors.text}"
    typography: "{typography.label}"
    rounded: "{rounded.none}"
    padding: "4px 10px"
  input:
    backgroundColor: "{colors.band}"
    textColor: "{colors.text}"
    typography: "{typography.body}"
    rounded: "{rounded.none}"
    padding: "8px 12px"
  filter-on:
    backgroundColor: "{colors.text}"
    textColor: "{colors.band}"
    typography: "{typography.label}"
    rounded: "{rounded.none}"
    padding: "4px 12px"
  tag-sure:
    backgroundColor: "{colors.live}"
    textColor: "{colors.on-live}"
    rounded: "{rounded.none}"
    padding: "3px 7px"
  chip-play-around:
    backgroundColor: "{colors.team-red-wash}"
    textColor: "{colors.text}"
    rounded: "{rounded.none}"
    padding: "4px 12px 4px 4px"
---

# Design System: DeckLens

## Overview

**Creative North Star: "The Broadcast of Your Own Match"**

DeckLens is dressed like the graphics package on a match broadcast: two teams in solid colour bands, a VS mark between them, a score bug that says how the match is going, and hot yellow for whatever is live or needs watching. It runs full-screen on a second monitor while you play, so everything that matters mid-match has to be read in one glance from arm's length: who they are, their deck, what to play around, your odds.

It's flat and squared. Depth comes from bands of colour and from rules, never from soft cards or shadows. Names, numbers, labels and controls use one condensed display face in caps. Running text uses the system sans. Studio white (light) and broadcast ink (dark) are both first-class themes, and on the second monitor dark is the usual one.

**Key Characteristics:**
- Blue is always you, red is always them, on every surface.
- Hot yellow means live or watch out (the watcher's live state, an auto-picked opponent, the Play around label, the selected page). Nothing else uses it.
- Solid bands with angled clip-path ends; squared corners everywhere.
- Big condensed numerals for anything you read at a glance: names, odds, win rates, elixir.
- One authored motion: the VS bar landing when an opponent is found.

## Colors

Two team colours on a neutral ground, plus one signal colour and the game's own elixir purple.

### Primary
- **Team Blue** (team-blue; dark: team-blue-dark): you. Your side of the VS bar, your deck's rule, your share of the odds bar, the primary button, links and win rates that lean your way (team-blue-ink-dark as text on dark).
- **Team Red** (team-red; dark: team-red-dark): them. Their side of the VS bar, their deck's rule, their share of the odds bar, the Scout page's search button, win rates that lean their way (team-red-ink as text).

### Secondary
- **Live Yellow** (live; dark: live-dark): live and watch out. A fill only, always with on-live ink on it. It's the focus ring in dark mode too.
- **Elixir Purple** (elixir): the elixir drop on every card. It comes from the game, not the brand.

### Neutral
- **Ground** (ground / ground-dark): the page.
- **Band** and **Band 2** (band, band-2): flat surfaces that sit on the ground, such as the top bar, card tiles, the odds box and table row labels.
- **Rule** (rule): decorative dividers only.
- **Edge** (edge): control outlines. They reach 3:1 or more on every ground.
- **Text** and **Muted** (text, muted): running text and secondary facts.
- **Washes** (team-blue-wash, team-red-wash): tinted chip grounds, like the Play around chips.

### Named Rules
**The Team Colour Rule.** Blue means you and red means them, with no exceptions. Never use blue or red decoratively, and never for good or bad in general. A win rate turns blue because it favours you.

**The One Signal Rule.** Yellow is reserved for live or watch out. If something isn't live, isn't an auto-pick and isn't a threat to play around, it isn't yellow.

**The Honest Bar Rule.** A bar that shows odds or a win rate is filled exactly to its value. When a number won't fit in a narrow side, the label overflows onto the other colour; the bar never widens to make room.

## Typography

**Display Font:** Barlow Condensed 700 (bundled locally as a woff2 under the OFL; falls back to Bahnschrift, then Arial Narrow)
**Body Font:** the system sans (system-ui, Segoe UI, Roboto)

**Character:** The condensed caps of a broadcast lower-third, set against a plain, invisible reading face. Every number uses tabular figures.

### Hierarchy
- **Score** (700, clamp(3rem, 5vw, 4.5rem), 0.9): the odds numerals in the score bug.
- **Display** (700, clamp(1.75rem, 3.2vw, 3rem), 1): player names in the VS bar. These keep their own case, because names are data.
- **Headline** (700, 2rem, 1.05, uppercase): page headings.
- **Title** (700, 1.125rem, 0.04em, uppercase): section labels such as THEIR DECK, PLAY AROUND and ODDS, plus primary buttons.
- **Body** (400, 1rem, 1.5): running text.
- **Fact** (400, 0.875rem, 1.5): player and deck facts, notes, asides.
- **Caption** (400, 0.75rem, 1.4): game counts and ranges in tables, card levels, the footer disclaimer.
- **Label** (700, 0.9375rem, 0.05em, uppercase): quiet buttons, filters, disclosures and the watcher bug.

### Named Rules
**The Names Are Data Rule.** Player and clan names are never uppercased, truncated mid-glyph or styled by content. They're shown in bdi with ellipsis overflow, because they can be emoji, right-to-left text or any script.

## Layout

There's one centred column, at most 1760px wide (the page spacing token), with a fluid gutter of 16 to 40px. At 1600px and wider, the root font size goes up to 17px so the second monitor reads from arm's length.

The Scout board is the main layout:
- A strip of tools sits above the VS bar: Wrong player? and the mode tabs on the left, the auto-scout switch on the right.
- Under the VS bar is a two-column grid at 1000px and wider (5fr / 3fr). Their deck is the hero on the left; Play around, the odds and history are on the right.
- Your deck and the matchup record sit below that.
- Card art grows with the viewport but is capped by its height, so both rows of their deck fit in the first view at 1920×1080.

Spacing works in 4px steps (4, 8, 12, 20), with clamp()-ed section gaps. On narrow screens everything stacks into one column.

## Elevation & Depth

There are no shadows. Depth comes from flat tonal layers (ground, then band, then band 2) and from solid colour bands. Control edges are drawn as 1px inset box-shadow outlines, which are strokes rather than elevation, and they thicken to 2px on hover.

### Named Rules
**The Flat Broadcast Rule.** Nothing floats. If a surface needs to stand out, give it a band colour, a team colour or a rule, never a drop shadow or blur.

## Shapes

Corners are squared everywhere (0px). Angles come from clip-path: the VS bar's sides lean toward each other by 18px, and the wordmark's two bars are skewed -14°. Team ownership is shown with a 4px rule across the top of a deck, and a card's rarity with a 4px bottom edge, as in the game. The elixir drop (a rotated square with three rounded corners) is the only curved form, and it belongs to the game's own icon.

## Components

### Buttons
- **Shape:** squared (0px).
- **Primary:** a solid Team Blue band with white display caps, padded 8px 18px. On Scout, where the action is about them, the band is Team Red.
- **Hover / Focus:** the band darkens by mixing in 14% black. Focus is a 3px ring in the focus colour, offset 2px.
- **Quiet:** transparent, with a 1px inset Edge outline that becomes a 2px Text outline on hover. It's used for secondary actions such as Wrong player?, Read again and Go to Scout. On a team band (on-band) the outline is white.

### Chips
- **Play around:** a red-wash ground with the elixir drop and the card name in display type. These are their spells and buildings, grouped by kind.
- **Tags:** an Auto-picked tag (sure) is a yellow fill. A Best guess tag (doubt) is a 2px white outline on the red band.

### Inputs / Fields
- **Style:** a band background with a 1px inset Edge outline and squared corners. The label sits above the input.
- **Focus:** the global focus ring, offset 0.

### Navigation
- **Page tabs:** muted display caps in the top bar. The selected tab turns Text colour and gets a 4px yellow underline.
- **Sub-tabs:** display caps on a rule. The open tab gets a 3px Text underline.
- **Filters:** a segmented row of squared buttons. The pressed one is inverted (Text background, Band text).

### The VS Bar (signature)
A blue band and a red band leaning into each other, with a squared VS mark between them. The blue side shows your name and your deck's average elixir first. The red side shows their name, facts and a certainty tag. When an opponent is found, the bar lands: each side slides 24px in from its edge and the mark pops in. With Reduce Motion on, it just appears.

### The Score Bug (signature)
The odds box: a band with ODDS and the game count, then one bar filled with Team Blue exactly to your win chance on a Team Red ground, with your number large on the left and theirs on the right, both in white. The archetype names sit under it. The matchup record table uses the same idea in small: blue column headers (the cells are your win rate) and a 6px blue/red bar under each rate.

### The Watcher Bug
A compact status in the top bar: a state block (yellow when live, outlined in amber or red when there's a problem) and what was last read off the phone.

## Do's and Don'ts

### Do:
- **Do** put you in blue and them in red on every new surface, including charts and share images.
- **Do** fill every proportional bar exactly to its value (The Honest Bar Rule).
- **Do** set glance numbers (odds, rates, elixir, names) in the display face, big.
- **Do** show calm, missing-setup states as one plain line plus one next step, keeping developer setup text behind a Setup disclosure for whoever runs DeckLens locally.
- **Do** keep every colour pair at WCAG AA in both themes, and keep the increased-contrast overrides.

### Don't:
- **Don't** use drop shadows, blurs, gradients as decoration, or rounded cards.
- **Don't** use yellow for anything that isn't live or watch out.
- **Don't** put coloured side stripes on cards or list items. Ownership is a top rule, selection is an outline.
- **Don't** add eyebrow or kicker labels above headings.
- **Don't** load fonts, scripts or images from other hosts. The CSP allows only the site itself.
