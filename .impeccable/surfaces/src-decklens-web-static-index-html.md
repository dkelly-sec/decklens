---
version: 1
slug: "src-decklens-web-static-index-html"
primary_target: "src/decklens/web/static/index.html"
related_targets: ["src/decklens/web/static/app.js","src/decklens/web/static/style.css","src/decklens/web/static/login.html"]
---

# Surface: DeckLens web app (You, Scout, Meta, login)

Mode: Operate. Audience: the owner now, other signed-up players later. Job: mid-match, know the opponent's deck, what to play around and the odds in one glance on a second monitor; between matches, review record, rank and meta. Constraints: plain HTML/CSS/JS, strict CSP (no inline, no external hosts), hostile names, features that need the database or watcher must read as working when absent.

Memorable moment: the VS bar landing when the watcher finds your opponent.

## Direction contract

THESIS: DeckLens is the broadcast of your own match: when a battle starts the screen becomes a versus graphic, you in blue on the left, them in red on the right, the odds in a score bug. It refuses the category default of a centred column of identical cards with explainer paragraphs.

OWN-WORLD: Esports broadcast graphics. Deep ink ground (blue-black), team blue and team red as solid bands that own their side, white type, one hot yellow for live and attention. Barlow Condensed in caps for names, numbers and labels; system sans for running text. Hard edges, no soft shadows, no rounded cards; panels are flat bands separated by rules and space. Light theme: the same system on bright studio white with deeper team colours.

STORY: The visitor sees whether DeckLens is watching, then who they face, their deck, the spells and buildings to play around, and the odds, without reading a sentence. Doubt (best guess, misread, hidden card) is labelled on the graphic. Everything else, the form, other candidates, history, waits one click away.

FIRST VIEWPORT: Top bar: DeckLens wordmark left, You / Scout / Meta tabs as broadcast segment labels, watcher score bug right (live dot, device, state). Scout in battle: full-width VS bar (your name and deck elixir in a blue band, VS chip, their name, clan, rating, confidence tag in a red band). Below, two columns: left wide, their 8 cards large in a 4x2 grid with average elixir and cycle; right, PLAY AROUND (their spells and buildings) above the odds score bug (you 77 / them 23 with the split bar) and one history line. Under the fold: your deck, matchup detail, Wrong player / find someone else.

FORM: Broadcast versus, my top-ranked candidate (1 of 7), chosen by the user over the roll's assignment (7, tactics board). seed:7552e5302a06c550

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
