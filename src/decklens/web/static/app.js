// DeckLens front end.
// Safety rule: data from the server is only ever put on the page with
// textContent (via el()), never innerHTML, so a player named "<script>..."
// shows up as plain text. The Content Security Policy blocks inline script too.
"use strict";

const state = { me: null };

// --- small helpers -----------------------------------------------------------

function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = String(text);
  if (className) node.className = className;
  return node;
}

function show(container, ...children) {
  container.replaceChildren(...children);
}

function pct(x) {
  return Math.round(x * 100) + "%";
}

function storageGet(key) {
  try { return window.localStorage.getItem(key); } catch { return null; }
}

function storageSet(key, value) {
  try { window.localStorage.setItem(key, value); } catch { /* storage unavailable: fine */ }
}

async function api(path, options) {
  const response = await fetch(path, options);
  if (response.status === 401) {  // session expired or logged out elsewhere
    window.location.replace("/login");
    throw new Error("Please log in");
  }
  let body = null;
  try { body = await response.json(); } catch { /* not JSON */ }
  if (!response.ok) {
    let message = "Something went wrong";
    if (body && typeof body.detail === "string") message = body.detail;
    else if (response.status === 422) message = "That input isn't valid";
    else if (response.status === 429) message = "Too many requests, wait a moment";
    throw new Error(message);
  }
  return body;
}

function tagPath(tag) {
  return encodeURIComponent(tag.trim());
}

function errorBox(error) {
  return el("p", error.message, "error");
}

// --- rendering ---------------------------------------------------------------

const FORM_LABELS = { evo: "Evo", hero: "Hero", either: "Evo or Hero" };

function cardTile(card) {
  const classes = ["card"];
  if (card.rarity) classes.push(card.rarity);
  if (card.form) classes.push("form-" + card.form);
  if (card.hidden) classes.push("hidden-card");
  const tile = el("li", null, classes.join(" "));

  if (card.hidden) {
    // The API leaves out a Hero in the Hero slot: show the gap honestly.
    tile.append(el("span", "?", "card-placeholder"), el("span", card.name, "card-name"),
      el("span", "The game's API doesn't show this card", "card-facts"));
    return tile;
  }

  const img = document.createElement("img");
  const form = card.form === "evo" || card.form === "hero" ? "?form=" + card.form : "";
  img.src = "/cards/" + encodeURIComponent(card.id) + ".png" + form;
  img.alt = card.name;
  img.width = 64;
  img.height = 77;
  img.loading = "lazy";
  img.addEventListener("error", () => img.remove());  // no image: the name below still shows
  tile.append(img, el("span", card.name, "card-name"));

  const facts = [];
  if (card.elixir !== null && card.elixir !== undefined) facts.push(card.elixir + " elixir");
  if (FORM_LABELS[card.form]) facts.push(FORM_LABELS[card.form]);
  if (card.below_max === 0) facts.push("Max level");
  else if (card.below_max) facts.push(card.below_max + " below max");
  if (facts.length) tile.append(el("span", facts.join(" · "), "card-facts"));
  return tile;
}

function plural(n, one, many) {
  return n + " " + (n === 1 ? one : many);
}

function deckView(deck) {
  const box = el("div", null, "deck-box");
  const s = deck.summary || {};
  const facts = [];
  if (s.avg_elixir !== null && s.avg_elixir !== undefined) facts.push("Average elixir " + s.avg_elixir);
  if (s.cycle !== null && s.cycle !== undefined) facts.push("4-card cycle " + s.cycle);
  if (s.evolutions) facts.push(plural(s.evolutions, "evolution", "evolutions"));
  if (s.heroes) facts.push(plural(s.heroes, "hero", "heroes"));
  if (s.either) facts.push(s.either + " evo or hero");
  if (s.fixed_levels) facts.push("Levels set by the challenge");
  else if (s.maxed !== null && s.maxed !== undefined) facts.push(s.maxed + "/" + s.levels_known + " cards maxed");
  if (facts.length) box.append(el("p", facts.join(" · "), "deck-facts"));
  const list = el("ul", null, "deck");
  for (const card of deck.cards) list.append(cardTile(card));
  box.append(list);
  if (s.hidden) {
    box.append(el("p", "One card is hidden: the Clash Royale API leaves out a Hero in the " +
      "Hero slot, so elixir figures use the other seven.", "detail"));
  }
  if (s.either) {
    box.append(el("p", "\"Evo or Hero\": the Wild slot card has both unlocked, and the API " +
      "doesn't say which one was active.", "detail"));
  }
  return box;
}

function knownIds(deck) {
  return deck.cards.filter((c) => c.id !== null && c.id !== undefined).map((c) => c.id);
}

function seenText(minutes) {
  if (minutes < 2) return "online now";
  if (minutes < 60) return "seen " + minutes + " min ago";
  if (minutes < 48 * 60) return "seen " + Math.round(minutes / 60) + " h ago";
  return "seen " + Math.round(minutes / 1440) + " days ago";
}

// Names and clans go in <bdi> so right-to-left text (Arabic, Hebrew) stays
// inside its own part and can't reorder the tag and numbers around it.
function playerLine(player, options = {}) {
  const line = el("p", null, "player");
  const parts = [el("bdi", player.name || "Unknown"), player.tag];
  if (player.clan) parts.push(el("bdi", player.clan));
  else if (options.showNoClan) parts.push("no clan");
  if (player.rank) parts.push("rank #" + player.rank);
  if (player.rating) parts.push("rating " + player.rating);
  if (player.trophies) parts.push(player.trophies.toLocaleString() + " trophies");
  if (player.last_seen_minutes !== null && player.last_seen_minutes !== undefined) {
    parts.push(seenText(player.last_seen_minutes));
  }
  parts.forEach((part, i) => {
    if (i) line.append(" · ");
    line.append(part);  // strings become text nodes: still never HTML
  });
  return line;
}

const COLUMN_NAMES = { 200: "Top 200", 1000: "Top 1,000" };
const LEVEL_NAMES = {
  exact: "Exact",
  7: "7+ of 8",
  6: "6+ of 8",
  archetype: "Archetype",
};

function columnName(tier) {
  return tier === null ? "All players" : COLUMN_NAMES[tier] || "Top " + tier.toLocaleString();
}

function recordCell(cell) {
  const td = el("td", null, "record");
  if (cell.mirror) {
    td.append(el("span", "Mirror", "rate"), el("span", plural(cell.games, "game", "games"), "games"));
  } else if (cell.games) {
    td.append(el("span", pct(cell.rate), "rate"));
    const games = el("span", plural(cell.games, "game", "games"), "games");
    if (cell.games < 30) games.append(el("span", " (few)", "few"));
    td.append(games, el("span", pct(cell.range[0]) + "–" + pct(cell.range[1]), "range"));
  } else {
    td.append(el("span", "—", "rate"), el("span", "0 games", "games"));
  }
  return td;
}

// Rows: how closely decks must match. Columns: skill tier. Cells: your win rate.
function breakdownTable(b) {
  const wrap = el("div", null, "table-wrap");
  const table = el("table", null, "tiers");
  table.append(el("thead"), el("tbody"));
  const head = el("tr");
  head.append(el("th", "Match"));
  for (const cell of b.levels[0].cells) head.append(el("th", columnName(cell.tier)));
  table.tHead.append(head);
  for (const level of b.levels) {
    const tr = el("tr");
    tr.append(el("th", LEVEL_NAMES[level.level] || level.level));
    for (const cell of level.cells) tr.append(recordCell(cell));
    table.tBodies[0].append(tr);
  }
  wrap.append(table);
  return wrap;
}

// Without the database, only the archetype record from training is available.
function recordOnly(m) {
  const table = el("table", null, "tiers");
  table.append(el("thead"), el("tbody"));
  const head = el("tr");
  head.append(el("th", "Match"), el("th", "All stored battles"));
  table.tHead.append(head);
  const tr = el("tr");
  tr.append(el("th", LEVEL_NAMES.archetype));
  if (m.record) {
    tr.append(recordCell({ games: m.record.games, rate: m.record.wins / m.record.games,
      range: m.record.range }));
  } else {
    tr.append(recordCell({ games: m.games, mirror: true }));
  }
  table.tBodies[0].append(tr);
  return table;
}

function matchupBox(m, label, mode) {
  const box = el("div", null, "matchup");
  box.append(el("p", label || "Matchup", "label"));
  box.append(el("p", m.archetype_a + "  vs  " + m.archetype_b, "archetypes"));
  box.append(el("p", "Your deck wins about " + pct(m.win_chance), "chance"));
  if (m.breakdown) {
    box.append(breakdownTable(m.breakdown));
    box.append(el("p", "Your win rate, from ranked games in the last " + m.breakdown.window_days +
      " days. Exact: both decks card for card. 7+ / 6+ of 8: each deck shares at least that " +
      "many cards with yours and theirs. Archetype: same deck styles. Top 200 / 1,000 only " +
      "count games where both players were in that bracket (ranks beyond 500 come from " +
      "that day's leaderboard).", "detail"));
  } else if (m.record || m.mirror) {
    box.append(recordOnly(m));
  }
  if (m.note) box.append(el("p", m.note, "note"));
  if (mode === "challenge") {
    box.append(el("p", "These numbers come from Ranked games (challenge games aren't collected), " +
      "so treat them as a rough guide for the challenge.", "note"));
  } else if (mode === "trophy") {
    box.append(el("p", "These numbers come from Ranked games (Trophy Road games aren't collected), " +
      "so treat them as a rough guide. Card levels matter more on Trophy Road.", "note"));
  }
  if (m.key_cards && m.key_cards.length) {
    const cards = el("ul", null, "key-cards");
    for (const k of m.key_cards) {
      const where = k.deck === "yours" ? "in your deck" : "in their deck";
      const effect = k.effect > 0 ? "helps you" : "helps them";
      cards.append(el("li", k.card + " (" + where + ") " + effect));
    }
    box.append(el("p", "Cards that matter most:", "detail"), cards);
  }
  return box;
}

// --- you ---------------------------------------------------------------------

// A tag uses only these characters (the letter O is accepted as zero).
const TAG_PATTERN = /^#?[0289PYLQGRJCUVO]{3,15}$/i;

function asTag(text) {
  const t = text.trim();
  if (!TAG_PATTERN.test(t)) return null;
  return "#" + t.replace(/^#/, "").toUpperCase().replace(/O/g, "0");
}

async function showMe(tag) {
  const out = document.getElementById("me-result");
  show(out, el("p", "Looking up…", "muted"));
  try {
    const me = await api("/api/players/" + tagPath(tag));
    state.me = me;
    state.myDecks = undefined;  // your Trophy Road / challenge decks: fetched again for this player
    storageSet("decklens.myTag", me.tag);
    const parts = [playerLine(me)];
    if (me.deck.cards.length) parts.push(el("p", "Current deck", "label"), deckView(me.deck));
    show(out, ...parts);
  } catch (error) {
    state.me = null;
    state.myDecks = undefined;
    show(out, errorBox(error));
  }
}

function pickButton(content, onPick) {
  const item = el("li");
  const button = el("button", null, "result-button");
  button.type = "button";
  button.append(content);
  button.addEventListener("click", onPick);
  item.append(button);
  return item;
}

// "#..." looks up a tag straight away. Anything else searches names, and if
// the text could also be a tag, that is offered as the first choice.
async function lookUpMe(event) {
  if (event) event.preventDefault();
  const out = document.getElementById("me-result");
  const text = document.getElementById("me-tag").value;
  const tag = asTag(text);
  if (text.trim().startsWith("#")) {
    if (tag) await showMe(tag);
    else show(out, el("p", "That isn't a valid player tag.", "error"));
    return;
  }
  show(out, el("p", "Searching…", "muted"));
  let results = [];
  let searchError = null;
  try {
    results = (await api("/api/search?name=" + encodeURIComponent(text))).results;
  } catch (error) {
    searchError = error;
  }
  if (!results.length && !tag) {
    show(out, searchError ? errorBox(searchError) : el("p", "No players found with that name. " +
      "DeckLens only knows players it has seen in ranked games, so try your tag instead " +
      "(it's under your name on your profile).", "muted"));
    return;
  }
  const list = el("ul", null, "results");
  if (tag) list.append(pickButton(el("p", "Use player tag " + tag, "player"), () => showMe(tag)));
  for (const r of results) list.append(pickButton(playerLine(r), () => showMe(r.tag)));
  show(out, el("p", "Tap yourself:", "hint"), list);
}

// --- last opponent -----------------------------------------------------------

function lastOpponentParts(opp) {
  const parts = [
    playerLine(opp),
    el("p", (MODE_NAMES[opp.mode] || "Battle") + " · You " + opp.your_result + " this one.", "detail"),
    el("p", "Their deck", "label"),
    deckView(opp.deck),
  ];
  if (opp.matchup) parts.push(matchupBox(opp.matchup, "Your deck vs theirs", opp.mode));
  return parts;
}

async function scoutLast() {
  const out = document.getElementById("last-result");
  if (!state.me) {
    show(out, el("p", "Look yourself up first.", "error"));
    return;
  }
  show(out, el("p", "Checking your battle log…", "muted"));
  try {
    const opp = await api("/api/players/" + tagPath(state.me.tag) + "/last-opponent");
    show(out, ...lastOpponentParts(opp));
  } catch (error) {
    show(out, errorBox(error));
  }
}

// --- find an opponent --------------------------------------------------------

function resultList(players) {
  const list = el("ul", null, "results");
  for (const r of players) {
    const item = el("li");
    const button = el("button", null, "result-button" + (r.active ? " active" : ""));
    button.type = "button";
    button.dataset.confident = String(Boolean(r.active || r.clan_match));
    button.append(playerLine(r, { showNoClan: "clan" in r }));
    button.addEventListener("click", () => showOpponentDecks(r, item));
    item.append(button);
    list.append(item);
  }
  return list;
}

// Asks the game directly (not our index): recently active members of clans
// with this name. For alts and Trophy Road players DeckLens hasn't seen.
// Returns the nodes to show.
async function liveClanParts(clan, name) {
  const params = new URLSearchParams({ clan });
  if (name) params.set("name", name);
  try {
    const found = await api("/api/clan-lookup?" + params.toString());
    if (!found.clans_found) {
      return [el("p", "The game has no clan called \u201c" + clan + "\u201d. Check the spelling.", "muted")];
    }
    if (!found.players.length) {
      return [el("p", "Nobody" + (name ? " with a name like that" : "") + " in " +
        plural(found.clans_checked.length, "clan", "clans") + " called \u201c" + clan + "\u201d.", "muted")];
    }
    const note = found.players.some((p) => p.active)
      ? "Your opponent is probably near the top: they're playing right now."
      : "Nobody here has been online in the last 15 minutes. It may be a different clan with the same name.";
    return [el("p", "Checked " + plural(found.clans_checked.length, "clan", "clans") +
      " called \u201c" + clan + "\u201d in the game" + (name ? ", best name match first" : "") +
      ", then most recently online. " + note, "hint"), resultList(found.players)];
  } catch (error) {
    return [errorBox(error)];
  }
}

async function liveClanLookup(clan, name, out) {
  show(out, el("p", "Checking clans named \u201c" + clan + "\u201d in the game…", "muted"));
  show(out, ...(await liveClanParts(clan, name)));
}

const NOT_KNOWN_NO_CLAN = "They're probably not in DeckLens yet, and without a clan the game " +
  "can't be asked (it only searches by clan). After the battle, \u201cScout my last opponent\u201d shows their deck.";

// Auto-scout picks the player for you: the first confident match (in the clan
// that was read, or online right now), else the top result as a best guess.
// Results are already sorted best-first. You can still tap someone else.
function autoOpen(out, allowGuess = true) {
  const buttons = [...out.querySelectorAll(".result-button")];
  const pick = buttons.find((b) => b.dataset.confident === "true") || (allowGuess ? buttons[0] : null);
  if (!pick) return;
  const sure = pick.dataset.confident === "true";
  for (const b of buttons) b.classList.remove("auto-picked");
  pick.classList.add("auto-picked");
  pick.prepend(el("span", sure ? "Auto-picked" : "Best guess", "pick-badge"));
  pick.click();
  if (pick.scrollIntoView) pick.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function findOpponent(event, options = {}) {
  if (event) event.preventDefault();
  const out = document.getElementById("find-result");
  const name = document.getElementById("find-name").value.trim();
  const clan = document.getElementById("find-clan").value.trim();
  if (!name && !clan) {
    show(out, el("p", "Type some of their name, their clan, or both.", "error"));
    return;
  }
  // The screen watcher saw no clan under their name: that's a clue, so
  // players without a clan come first. (An empty box you left blank isn't.)
  const noClan = Boolean(options.noClan && name && !clan);
  const params = new URLSearchParams();
  if (name) params.set("name", name);
  if (clan) params.set("clan", clan);
  if (noClan) params.set("no_clan", "1");
  let path = "/api/search?" + params.toString();
  // Players nearest their rating (read off the screen) come first, else nearest
  // yours, as Ranked pairs similar ratings.
  const near = options.rating || (state.me && state.me.rating);
  if (near) path += "&near_rating=" + encodeURIComponent(near);
  show(out, el("p", "Searching…", "muted"));
  let results = [];
  try {
    results = (await api(path)).results;
  } catch (error) {
    if (clan.length < 3) {
      show(out, errorBox(error));
      return;
    }
  }
  const canGoLive = clan.length >= 3;
  if (!results.length) {
    if (canGoLive) {
      await liveClanLookup(clan, name, out);  // not in our index: ask the game
      if (options.auto) autoOpen(out, !options.nameUnread);
    } else if (noClan) {
      show(out, el("p", "No players found. " + NOT_KNOWN_NO_CLAN, "muted"));
    } else {
      show(out, el("p", "No players found. If their name is emoji or a language you can't type, " +
        "search by their clan instead.", "muted"));
    }
    return;
  }
  if (noClan) {
    if (results.some((r) => r.clan_match)) {
      show(out, el("p", "They have no clan, so players without one come first. Tap the right player:", "hint"),
        resultList(results));
      if (options.auto) autoOpen(out, !options.nameUnread);
    } else {
      // Everyone here has a clan, so none of them is likely to be the
      // opponent: list them, but don't open one as if it were.
      show(out, el("p", "Nobody DeckLens knows without a clan has a name like that. " + NOT_KNOWN_NO_CLAN, "hint"),
        el("p", "Closest names (all in clans, so probably not them):", "hint"), resultList(results));
    }
    return;
  }
  if (canGoLive && !results.some((r) => r.clan_match)) {
    // Nobody we know is in that clan: check the game first, keep the name
    // matches from other clans underneath in case the clan was misread.
    show(out, el("p", "Nobody DeckLens knows is in that clan. Checking the game…", "muted"));
    const live = await liveClanParts(clan, name);
    show(out, ...live, el("p", "Players with similar names in other clans:", "hint"), resultList(results));
    if (options.auto) autoOpen(out, !options.nameUnread);
    return;
  }
  const parts = [el("p", "Tap the right player:", "hint"), resultList(results)];
  if (canGoLive) {
    const live = el("button", "Not here? Check the clan live", "link-button");
    live.type = "button";
    live.addEventListener("click", () => liveClanLookup(clan, name, out));
    parts.push(live);
  }
  show(out, ...parts);
  if (options.auto) autoOpen(out, !options.nameUnread);
}

// --- auto-scout (screen watcher) -------------------------------------------

const auto = { lastId: null, timer: null, afterBattle: null, afterToken: 0 };
const AFTER_BATTLE_TRIES = 12;      // your battle log can take a minute or two to update
const AFTER_BATTLE_WAIT_MS = 10000;

function lastOpponentNow() {
  return api("/api/players/" + tagPath(state.me.tag) + "/last-opponent");
}

// The watcher saw a battle but couldn't read anything in the opponent's corner
// (a script the text reader doesn't know, like Arabic). Remember who your last
// opponent was before this battle, so the new one can be spotted afterwards.
function battleUnreadable(out) {
  if (!state.me) {
    auto.afterBattle = null;
    show(out, el("p", "In a battle, but their name can't be read (letters the screen reader doesn't " +
      "know, like Arabic, or emoji). Look yourself up under You, and DeckLens will show their deck " +
      "after the battle.", "hint"));
    return;
  }
  auto.afterBattle = { before: lastOpponentNow().then((o) => o.time, () => null) };
  show(out, el("p", "In a battle, but their name can't be read (letters the screen reader doesn't " +
    "know, like Arabic, or emoji). Their deck will show here when the battle ends.", "hint"));
}

async function showAfterBattle(out) {
  const token = ++auto.afterToken;
  const before = await auto.afterBattle.before;
  auto.afterBattle = null;
  show(out, el("p", "Battle over. Getting your opponent from your battle log…", "muted"));
  for (let attempt = 0; attempt < AFTER_BATTLE_TRIES; attempt++) {
    if (attempt) await new Promise((r) => setTimeout(r, AFTER_BATTLE_WAIT_MS));
    if (token !== auto.afterToken) return;  // a newer battle took over
    let opp = null;
    try {
      opp = await lastOpponentNow();
    } catch { /* not in the log yet, or offline for a moment */ }
    if (opp && opp.time !== before) {
      const more = el("button", "See their recent decks", "link-button");
      more.type = "button";
      const holder = el("div");
      more.addEventListener("click", () => showOpponentDecks(opp, holder));
      show(out, el("p", "Your opponent (their name couldn't be read during the battle):", "hint"),
        ...lastOpponentParts(opp), more, holder);
      return;
    }
  }
  if (token === auto.afterToken) {
    show(out, el("p", "Your battle log hasn't updated yet. Try \u201cScout my last opponent\u201d in a minute.", "muted"));
  }
}

// One line saying whether the screen watcher can see your phone, and why not.
const WATCHER_STATES = {
  connected: ["on", (d) => "Connected to " + (d || "your phone screen")],
  screen_only: ["warn", (d) => "Connected to " + (d || "your phone screen") +
    ", but keep that window visible: it can't be read behind other windows"],
  starting: ["idle", () => "Starting the screen reader…"],
  no_window: ["off", () => "Not connected: open your phone screen in Phone Link (or mirror your iPad to 5KPlayer)"],
  minimised: ["off", (d) => "Not connected: " + (d || "the mirror window") + " is minimised (it can sit behind other windows)"],
  unavailable: ["off", (d) => "Not connected: " + (d || "the screen watcher can't run")],
};

function showWatcherStatus(watcher, latest) {
  const status = document.getElementById("auto-status");
  const [kind, text] = watcher && WATCHER_STATES[watcher.state]
    ? [WATCHER_STATES[watcher.state][0], WATCHER_STATES[watcher.state][1](watcher.detail)]
    : ["off", "Not connected: the screen watcher isn't running"];
  let line = text;
  if (kind === "on" || kind === "warn") {
    if (latest && latest.kind === "unreadable") line += " · in a battle: their name can't be read";
    else if (latest && latest.kind !== "ended") {
      line += " · last opponent: " + (latest.name || "name unreadable") +
        (latest.clan ? " (" + latest.clan + ")" : "") + (latest.rating ? ", rated " + latest.rating : "");
    } else line += " · waiting for a battle";
  }
  show(status, el("span", null, "status-dot " + kind), el("span", line));
}

async function pollWatcher() {
  if (document.visibilityState !== "visible") return;
  let latest, watcher;
  try {
    ({ latest, watcher } = await api("/api/watch"));
  } catch {
    return;  // offline for a moment: try again next time
  }
  showWatcherStatus(watcher, latest);
  if (!latest) return;
  const fresh = auto.lastId === null ? latest.age_seconds < 60 : latest.id !== auto.lastId;
  auto.lastId = latest.id;
  if (!fresh) return;
  const out = document.getElementById("find-result");
  if (latest.kind === "unreadable") {
    if (out.scrollIntoView) out.scrollIntoView({ behavior: "smooth", block: "start" });
    battleUnreadable(out);
    return;
  }
  if (latest.kind === "ended") {
    if (auto.afterBattle) await showAfterBattle(out);
    return;
  }
  auto.afterBattle = null;  // this battle's opponent was read
  document.getElementById("find-name").value = latest.name || "";
  document.getElementById("find-clan").value = latest.clan || "";
  const results = document.getElementById("find-result");
  if (results.scrollIntoView) results.scrollIntoView({ behavior: "smooth", block: "start" });
  const rated = latest.rating ? " They're rated " + latest.rating + "." : "";
  if (!latest.name) {
    // Fancy letters or symbols OCR can't read. Search the clan, but only open
    // someone we're sure of: a guess from the clan alone is usually wrong.
    await findOpponent(null, { auto: true, nameUnread: true, rating: latest.rating });
    results.prepend(el("p", "Couldn't read their name (fancy letters or symbols), so this searched " +
      "their clan instead." + rated + " If nobody's marked, \u201cScout my last opponent\u201d " +
      "shows their deck after the battle.", "hint"));
    return;
  }
  await findOpponent(null, { auto: true, noClan: !latest.clan, rating: latest.rating });
}

function setAutoScout(on) {
  storageSet("decklens.autoScout", on ? "1" : "0");
  const status = document.getElementById("auto-status");
  status.hidden = !on;
  clearInterval(auto.timer);
  auto.timer = null;
  auto.lastId = null;
  if (on) {
    show(status, el("span", null, "status-dot idle"), el("span", "Checking the screen watcher…"));
    pollWatcher();
    auto.timer = setInterval(pollWatcher, 2000);
  }
}

const MODE_NAMES = { ranked: "Ranked", trophy: "Trophy Road", challenge: "20-win challenge" };
const MODES = ["ranked", "trophy", "challenge"];
let tabCounter = 0;

function emptyMessage(mode, recent) {
  const entries = Object.entries(recent || {});
  const total = entries.reduce((sum, [, n]) => sum + n, 0);
  if (!total) return "This player has no battles in their recent battle log.";
  const list = entries.map(([name, n]) => name + " " + n).join(", ");
  const name = MODE_NAMES[mode];
  if (recent[name]) return "Their recent " + name + " battles couldn't be read (" + list + ").";
  return "No " + name + " games in their last " + total + " battles. Recently they've played: " + list + ".";
}

// Which of your decks to compare against: your current deck for Ranked; for
// Trophy Road and the 20-win challenge, your latest deck in that mode (you may
// play a different deck there), falling back to your current deck.
const MY_MODE_LABELS = { trophy: "Trophy Road", challenge: "challenge" };

async function myDeckFor(mode) {
  if (mode === "ranked" || !state.me) {
    return state.me ? { deck: state.me.deck, label: "Your current deck vs this one" } : null;
  }
  if (state.myDecks === undefined) {  // your own log, fetched once
    state.myDecks = api("/api/players/" + tagPath(state.me.tag) + "/decks")
      .then((mine) => mine.modes || {}).catch(() => ({}));
  }
  const decks = ((await state.myDecks)[mode] || []).slice();
  decks.sort((a, b) => String(b.last_played).localeCompare(String(a.last_played)));
  const name = MY_MODE_LABELS[mode] || "matching";
  if (decks.length) return { deck: decks[0].deck, label: "Your latest " + name + " deck vs this one" };
  return { deck: state.me.deck, label: "Your current deck vs this one (no recent " + name + " deck of yours found)" };
}

async function deckList(decks, mode) {
  const parts = [];
  for (const d of decks) {
    const when = d.last_played ? ", last on " + new Date(d.last_played).toLocaleDateString() : "";
    parts.push(el("p", "Played " + plural(d.games, "time", "times") + " (" + d.wins + " wins" + when + ")", "label"),
      deckView(d.deck));
    const mine = await myDeckFor(mode);
    if (mine && knownIds(mine.deck).length >= 7 && knownIds(d.deck).length >= 7) {
      try {
        const m = await api("/api/predict", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ deck_a: knownIds(mine.deck), deck_b: knownIds(d.deck) }),
        });
        parts.push(matchupBox(m, mine.label, mode));
      } catch (error) {
        parts.push(errorBox(error));
      }
    }
  }
  return parts;
}

function newestPlay(decks) {
  return decks.reduce((best, d) => (String(d.last_played) > best ? String(d.last_played) : best), "");
}

async function showOpponentDecks(player, container) {
  const panel = el("div", "Loading their recent decks…", "decks-panel");
  const old = container.querySelector(".decks-panel");
  if (old) old.remove();
  container.append(panel);
  let result;
  try {
    result = await api("/api/players/" + tagPath(player.tag) + "/decks");
  } catch (error) {
    show(panel, errorBox(error));
    return;
  }
  const modes = result.modes || {};

  // One tab per mode. Opens on whichever mode they played most recently.
  const id = "decks-" + (++tabCounter);
  const tabs = el("div", null, "tabs");
  tabs.setAttribute("role", "tablist");
  tabs.setAttribute("aria-label", "Game mode");
  const body = el("div", null, "tab-panel");
  body.setAttribute("role", "tabpanel");
  body.id = id + "-panel";
  const rendered = {};
  const buttons = {};

  async function select(mode) {
    for (const m of MODES) buttons[m].setAttribute("aria-selected", String(m === mode));
    body.setAttribute("aria-labelledby", buttons[mode].id);
    if (!rendered[mode]) {  // built once per tab; a promise, so fast clicks don't build twice
      const decks = modes[mode] || [];
      rendered[mode] = decks.length ? deckList(decks, mode)
        : Promise.resolve([el("p", emptyMessage(mode, result.recent_modes), "muted")]);
    }
    show(body, el("p", "Loading matchups…", "muted"));
    const nodes = await rendered[mode];
    if (buttons[mode].getAttribute("aria-selected") === "true") show(body, ...nodes);
  }

  for (const mode of MODES) {
    const games = (modes[mode] || []).reduce((n, d) => n + d.games, 0);
    const button = el("button", MODE_NAMES[mode] + " · " + plural(games, "game", "games"), "tab");
    button.type = "button";
    button.id = id + "-" + mode;
    button.setAttribute("role", "tab");
    button.setAttribute("aria-controls", body.id);
    button.addEventListener("click", () => select(mode));
    buttons[mode] = button;
    tabs.append(button);
  }
  show(panel, tabs, body);
  const first = MODES.slice().sort((a, b) => newestPlay(modes[b] || []).localeCompare(newestPlay(modes[a] || [])))[0];
  await select(first);
}

// --- start -------------------------------------------------------------------

async function showAccount() {
  try {
    const me = await api("/api/me");
    if (me.desktop) setUpDesktop();
    if (!me.login) return;  // logins switched off (local use only)
    document.getElementById("account-name").textContent = me.username;
    document.getElementById("account").hidden = false;
  } catch { /* api() already redirects to the login page on 401 */ }
}

// Inside the DeckLens desktop app: a Quit button, and Auto-scout on by default
// (the app runs the screen watcher itself) unless you've switched it off.
function setUpDesktop() {
  const quit = document.getElementById("quit-button");
  quit.hidden = false;
  quit.addEventListener("click", async () => {
    clearInterval(auto.timer);
    try {
      await fetch("/api/desktop/quit", { method: "POST" });
    } finally {
      document.body.replaceChildren(el("p", "DeckLens has closed. You can close this window.", "muted"));
      window.close();
    }
  });
  const box = document.getElementById("auto-scout");
  if (storageGet("decklens.autoScout") === null && !box.checked) {
    box.checked = true;
    setAutoScout(true);
  }
}

async function logOut() {
  try {
    await fetch("/api/logout", { method: "POST" });
  } finally {
    try { window.localStorage.removeItem("decklens.myTag"); } catch { /* fine */ }
    window.location.replace("/login");
  }
}

document.addEventListener("DOMContentLoaded", () => {
  showAccount();
  document.getElementById("logout-button").addEventListener("click", logOut);
  document.getElementById("me-form").addEventListener("submit", lookUpMe);
  document.getElementById("last-button").addEventListener("click", scoutLast);
  document.getElementById("find-form").addEventListener("submit", findOpponent);
  const autoBox = document.getElementById("auto-scout");
  autoBox.addEventListener("change", () => setAutoScout(autoBox.checked));
  autoBox.checked = storageGet("decklens.autoScout") === "1";
  if (autoBox.checked) setAutoScout(true);
  const saved = asTag(storageGet("decklens.myTag") || "");
  if (saved) {
    document.getElementById("me-tag").value = saved;
    showMe(saved);
  }
});
