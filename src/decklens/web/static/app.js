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

// Names and clans go in <bdi> so right-to-left text (Arabic, Hebrew) stays
// inside its own part and can't reorder the tag and numbers around it.
function playerLine(player) {
  const line = el("p", null, "player");
  const parts = [el("bdi", player.name || "Unknown"), player.tag];
  if (player.clan) parts.push(el("bdi", player.clan));
  if (player.rank) parts.push("rank #" + player.rank);
  if (player.rating) parts.push("rating " + player.rating);
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
    storageSet("decklens.myTag", me.tag);
    const parts = [playerLine(me)];
    if (me.deck.cards.length) parts.push(el("p", "Current deck", "label"), deckView(me.deck));
    show(out, ...parts);
  } catch (error) {
    state.me = null;
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

async function scoutLast() {
  const out = document.getElementById("last-result");
  if (!state.me) {
    show(out, el("p", "Look yourself up first.", "error"));
    return;
  }
  show(out, el("p", "Checking your battle log…", "muted"));
  try {
    const opp = await api("/api/players/" + tagPath(state.me.tag) + "/last-opponent");
    const parts = [
      playerLine(opp),
      el("p", (MODE_NAMES[opp.mode] || "Battle") + " · You " + opp.your_result + " this one.", "detail"),
      el("p", "Their deck", "label"),
      deckView(opp.deck),
    ];
    if (opp.matchup) parts.push(matchupBox(opp.matchup, "Your deck vs theirs", opp.mode));
    show(out, ...parts);
  } catch (error) {
    show(out, errorBox(error));
  }
}

// --- find an opponent --------------------------------------------------------

async function findOpponent(event) {
  event.preventDefault();
  const out = document.getElementById("find-result");
  const name = document.getElementById("find-name").value.trim();
  const clan = document.getElementById("find-clan").value.trim();
  if (!name && !clan) {
    show(out, el("p", "Type some of their name, their clan, or both.", "error"));
    return;
  }
  const params = new URLSearchParams();
  if (name) params.set("name", name);
  if (clan) params.set("clan", clan);
  let path = "/api/search?" + params.toString();
  // Ranked matches players of similar rating, so players nearest yours come first.
  if (state.me && state.me.rating) path += "&near_rating=" + encodeURIComponent(state.me.rating);
  show(out, el("p", "Searching…", "muted"));
  try {
    const { results } = await api(path);
    if (!results.length) {
      show(out, el("p", clan
        ? "No players found. Check the clan name, or try fewer letters of it."
        : "No players found. If their name is emoji or a language you can't type, " +
          "search by their clan instead.", "muted"));
      return;
    }
    const list = el("ul", null, "results");
    for (const r of results) {
      const item = el("li");
      const button = el("button", null, "result-button");
      button.type = "button";
      button.append(playerLine(r));
      button.addEventListener("click", () => showOpponentDecks(r, item));
      item.append(button);
      list.append(item);
    }
    show(out, el("p", "Tap the right player:", "hint"), list);
  } catch (error) {
    show(out, errorBox(error));
  }
}

const MODE_NAMES = { ranked: "Ranked", challenge: "20-win challenge" };
const MODES = ["ranked", "challenge"];
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

// Which of your decks to compare against: your current deck for Ranked, your
// latest 20-win challenge deck (an event deck) for the challenge.
async function myDeckFor(mode) {
  if (mode === "ranked" || !state.me) {
    return state.me ? { deck: state.me.deck, label: "Your current deck vs this one" } : null;
  }
  if (state.myChallenge === undefined) {
    state.myChallenge = null;
    try {
      const mine = await api("/api/players/" + tagPath(state.me.tag) + "/decks");
      const decks = (mine.modes && mine.modes.challenge) || [];
      decks.sort((a, b) => String(b.last_played).localeCompare(String(a.last_played)));
      if (decks.length) state.myChallenge = decks[0].deck;
    } catch { /* fall back to the current deck below */ }
  }
  if (state.myChallenge) return { deck: state.myChallenge, label: "Your latest challenge deck vs this one" };
  return { deck: state.me.deck, label: "Your current deck vs this one (no recent challenge deck of yours found)" };
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
    if (!me.login) return;  // logins switched off (local use only)
    document.getElementById("account-name").textContent = me.username;
    document.getElementById("account").hidden = false;
  } catch { /* api() already redirects to the login page on 401 */ }
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
  const saved = asTag(storageGet("decklens.myTag") || "");
  if (saved) {
    document.getElementById("me-tag").value = saved;
    showMe(saved);
  }
});
