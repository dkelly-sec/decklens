---
target: DeckLens web front end
total_score: 21
max_score: 40
na_heuristics: 
p0_count: 1
p1_count: 2
target_identity: "file:/home/claude/decklens/src/decklens/web/static/index.html"
target_fingerprint: "sha256:4c8ac582263d1adcbc3b04ef2d17cb9eaa3408f320c8d8258e6f4b98b4f9d0ba"
target_path: /home/claude/decklens/src/decklens/web/static/index.html
timestamp: 2026-11-07T20-33-41Z
slug: src-decklens-web-static-index-html
---
Method: dual-agent (A: design review · B: detector + browser evidence)

## Design Health Score
| # | Heuristic | Score | Key Issue |
|---|---|---|---|
| 1 | Visibility of System Status | 3 | misread name stated as fact in small muted line |
| 2 | Match System / Real World | 3 | game vocabulary good; "Tap" on PC, env-var names in UI |
| 3 | User Control and Freedom | 2 | auto-scout switches tab + double scroll; stale result not dismissable |
| 4 | Consistency and Standards | 2 | three identical nested tab bars; red overloaded |
| 5 | Error Prevention | 3 | strong validation and auto-pick rules |
| 6 | Recognition Rather Than Recall | 2 | "Look yourself up first"; table legend below table |
| 7 | Flexibility and Efficiency | 2 | no glance view; answer needs scrolling |
| 8 | Aesthetic and Minimalist Design | 1 | prose-heavy; form above the deck |
| 9 | Error Recovery | 1 | "Something went wrong", no retry; red developer 503s |
| 10 | Help and Documentation | 2 | always-on, partly developer-facing |
| Total | | 21/40 | Acceptable |

## Design Specificity Verdict
Generic SaaS shell (card-per-section 14px radius + double shadow style.css:111-118; heading accent bars 101-109; segmented pill tabs at three levels; slate/navy + #5b95ff palette 33-38; system-ui body; centred 720px column + tagline; stock buttons/ghost buttons/pill chips/spinner). Product-specific ideas (blue you / red them, tug-of-war, elixir drops, 4x2 deck) buried in results. Elixir drop upside down (style.css:223).
Detector: markup clean; 4 side-tab warnings style.css:208-211, all false positives (rarity stripes). Overlay blocked by app CSP (correct); with CSP bypassed in test browser, no anti-patterns on 6 views. Contrast: dark passes; light placeholder 3.95:1 fails; input borders ~1.3:1 and selected tab 1.07:1 in both themes.

## Priority Issues
- [P0] Mid-match answer buried: in a working auto-scout at 540px the opponent's deck starts ~1,550px down under stale result, form, 5 rematch rows, mode tabs. Fix: battle board at top of Scout (name/clan/confidence, deck large, play-around chips, win %, one-line history); fold the rest under "Wrong player?"; no double scroll. distill, layout.
- [P1] Generic SaaS shell hides the product. Fix: blue-vs-red versus band as spine, deck as hero, drop card shells/shadows, condensed face for numbers/tabs/labels, no tagline, flip elixir drop. bolder, typeset.
- [P1] Errors and misreads are dead ends ("Phone screen" searched as fact; "Something went wrong"; red WEB_DATABASE_URL messages). Fix: editable read chip + Read again; every error one action + Retry; calm muted not-set-up states. harden, clarify.
- [P2] Hierarchy and contrast: identical nested tabs, selected 1.07:1, borders ~1.3:1, empty modes tabbed, light placeholder/rarity fails. polish, colorize.
- [P2] Explanatory prose everywhere. distill, clarify.

## Persona Red Flags
Owner mid-match: answer is second section; 14px status; rematch before deck; two auto-scrolls; tab yank; two red primary buttons.
Portfolio reviewer: "Look yourself up first"; unexplained red watcher dot; red setup text on Meta/Tweaks.
Keyboard/screen reader: selection by colour only; invisible input edges; whole results announced; unannounced context change.

## Minor Observations
"Lost 1 in a row"; "Tap" on PC; login tagline/heading; halfway notch reads as gap; full-size footer; duplicate CSS rules; .status-dot.idle unstyled; centred column clears the phone mirror by luck in the real screenshot.

## Questions to Consider
Why does Scout open with a form when the watcher knows the opponent? Second-screen board instead of a centred page? Eight cards, two spells, one number, zero sentences?
