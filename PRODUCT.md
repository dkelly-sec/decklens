# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

- Today: the owner, a Clash Royale player in Ranked (Path of Legend), Trophy Road and the 20-win challenge, using it on his own PC while he plays.
- Near future (confirmed goal, not built yet): anyone who logs in and sets up their own account (their own player tag and screen watcher). There is no sign-up page today; accounts are made from the command line.
- The job: before and during a match, know who the opponent is, what deck they play, and how the matchup tends to go; between matches, review your own record, rank and decks, and the meta.

## Product Purpose

DeckLens scouts Clash Royale matchups. It looks up a player by tag or name, finds the current or last opponent, shows their deck and history against you, and estimates how your deck does against theirs from stored top-ladder battles. Success is a mid-match glance that answers "who is this, what do they play, what do I play around" in about a second.

## Positioning

Matchup estimates come from the owner's own crawl of top-ladder battles (archetype and card-level models), and a screen watcher reads the opponent's name and clan off the mirrored phone during the battle, so the opponent is found without typing.

## Operating Context

- Played on a phone, mirrored to a Windows PC with Phone Link (or an iPad via 5KPlayer); the screen watcher reads the mirror window.
- DeckLens runs full-screen on a second monitor while the owner plays (confirmed 2026-11-08). Glances happen mid-match, under time pressure, at arm's length.
- Served locally today (127.0.0.1, desktop app window in Edge app mode); an HTTPS deployment is planned.
- Three areas: You (own tag: current deck, today's session, record, rank history, deck tweaks), Scout (last opponent; find an opponent by name and clan; auto-scout from the watcher), Meta (archetype tier list by rank band).

## Capabilities and Constraints

- Front end is plain HTML, CSS and JavaScript served by FastAPI from `src/decklens/web/static/`; no framework or build step.
- Strict Content Security Policy: scripts, styles, images and fonts only from the site itself; no inline script or style; no external hosts. Fonts must be bundled locally.
- All DOM is built with textContent; player and clan names are hostile input and may be emoji, right-to-left, or any script.
- Card art is served through the app's own `/cards/{id}.png` proxy from the official asset host.
- Some features need the battle database (search, Meta, Tweaks) or the screen watcher (auto-scout); without them those areas must still read as working, not broken.
- Terminology from the game: elixir, 4-card cycle, Evo, Hero, archetype, Ranked / Path of Legend, Trophy Road, 20-win challenge, league, rating, clan.

## Brand Commitments

- Name: DeckLens.
- The Supercell Fan Content Policy disclaimer must appear on the site.

## Evidence on Hand

- Real data comes from the Clash Royale API and the owner's crawl; there are no testimonials, user counts or benchmarks, and none may be invented.
- A real screenshot of the app in use (Scout tab beside the Phone Link mirror) was reviewed on 2026-11-08.

## Product Principles

1. The mid-match answer comes first and fits one glance: opponent, deck, what to play around, odds.
2. Say how sure it is: auto-picked versus best guess, a misread name, a hidden card.
3. Never look broken: a missing database, watcher or result is a calm state with one next step, not developer text.
4. Hostile names are data: show them faithfully and safely, never let them break layout.
5. Built for the owner now, ready for other accounts later: nothing hardcoded to one player.

## Accessibility & Inclusion

Keep the existing keyboard tab navigation, visible focus, reduced-motion and increased-contrast support; text and control edges meet WCAG AA in both light and dark themes.
