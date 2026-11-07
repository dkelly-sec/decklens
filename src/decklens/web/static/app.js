// DeckLens front end.
// Safety rule: data from the server is only ever put on the page with
// textContent (via el()), never as HTML, so a player named "<script>..."
// shows up as plain text. The Content Security Policy blocks inline script too.
"use strict";

const state = { me: null, local: false };  // local: logins off or the desktop app

// --- small helpers -----------------------------------------------------------

// Tabs: only the selected one is a Tab stop; arrow keys move between them
// (and open the tab), Home and End jump to the ends, as tabs work everywhere.
function selectTab(tab, on) {
  tab.setAttribute("aria-selected", String(on));
  tab.tabIndex = on ? 0 : -1;
}

document.addEventListener("keydown", (event) => {
  const tab = event.target.closest && event.target.closest('[role="tab"]');
  const list = tab && tab.closest('[role="tablist"]');
  if (!list) return;
  const tabs = [...list.querySelectorAll('[role="tab"]')];
  const i = tabs.indexOf(tab);
  const next = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: tabs.length - 1 }[event.key];
  if (next === undefined) return;
  event.preventDefault();
  const target = tabs[(next + tabs.length) % tabs.length];
  target.focus();
  target.click();
});

// "Loading…" words with a sweeping bar, announced politely to screen readers.
function loadingText(text) {
  const p = el("p", text, "loading");
  p.setAttribute("role", "status");
  return p;
}

function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = String(text);
  if (className) node.className = className;
  return node;
}

function show(container, ...children) {
  container.replaceChildren(...children.filter(Boolean));
}

function action(text, onClick, className = "quiet") {
  const b = el("button", text, className);
  b.type = "button";
  b.addEventListener("click", onClick);
  return b;
}

function disclosure(summary, ...children) {
  const box = el("details");
  box.append(el("summary", summary), ...children);
  return box;
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
  let response;
  try {
    response = await fetch(path, options);
  } catch {
    throw Object.assign(new Error("Can't reach DeckLens. Check it's still running."), { status: 0 });
  }
  if (response.status === 401) {  // session expired or logged out elsewhere
    window.location.replace("/login");
    throw Object.assign(new Error("Please log in"), { status: 401 });
  }
  let body = null;
  try { body = await response.json(); } catch { /* not JSON */ }
  if (!response.ok) {
    let message = "The server couldn't do that (error " + response.status + ")";
    if (body && typeof body.detail === "string") message = body.detail;
    else if (response.status === 422) message = "That input isn't valid";
    else if (response.status === 429) message = "Too many requests: wait a moment";
    throw Object.assign(new Error(message), { status: response.status });
  }
  return body;
}

function tagPath(tag) {
  return encodeURIComponent(tag.trim());
}

// Something didn't work: say what, and offer the one thing to do about it.
// A feature this server isn't set up for is a calm state, not an error.
const SETUP_WORDS = /set up|WEB_DATABASE_URL|run python|Needs stored|model yet/i;

// next: an optional action for the not-set-up state (where to go instead).
function errorBox(error, retry, next) {
  if (error.status === 503 && SETUP_WORDS.test(error.message)) {
    const box = el("div", null, "message calm");
    const what = /model/i.test(error.message) ? "a trained matchup model" : "DeckLens's battle database";
    const p = el("p", "This needs " + what + ", which isn't connected on this server yet.");
    box.append(p);
    if (next) box.append(next);
    // Only whoever runs DeckLens can fix it: the setup step is for them, not every visitor.
    if (state.local) box.append(disclosure("Setup", el("p", error.message, "setup")));
    return box;
  }
  const box = el("div", null, "message problem");
  box.setAttribute("role", "alert");
  box.append(el("p", error.message));
  if (retry) box.append(action("Try again", retry));
  return box;
}

function calm(text, why, ...actions) {
  const box = el("div", null, "message calm");
  const p = el("p", text);
  if (why) p.append(el("span", why, "why"));
  box.append(p, ...actions);
  return box;
}

// --- cards and decks ---------------------------------------------------------

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
      el("span", "Hidden by the game's API", "card-facts"));
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
  if (card.elixir !== null && card.elixir !== undefined) tile.append(elixirDrop(card.elixir));

  const facts = [];
  if (FORM_LABELS[card.form]) facts.push(FORM_LABELS[card.form]);
  if (card.below_max === 0) facts.push("Max level");
  else if (card.below_max) facts.push(card.below_max + " below max");
  if (facts.length) tile.append(el("span", facts.join(" · "), "card-facts"));
  return tile;
}

// The game's elixir drop, with the cost inside it. Screen readers get "N elixir".
function elixirDrop(cost) {
  const drop = el("span", null, "drop");
  drop.setAttribute("role", "img");
  drop.setAttribute("aria-label", cost + " elixir");
  const n = el("span", cost);
  n.setAttribute("aria-hidden", "true");
  drop.append(n);
  return drop;
}

function plural(n, one, many) {
  return n + " " + (n === 1 ? one : many);
}

function fact(value, words) {
  const span = el("span");
  span.append(el("b", value), words);
  return span;
}

// side: "yours" or "theirs" (a blue or red edge). options.read: false leaves
// out the spells and buildings to play around (the board shows them beside it).
function deckView(deck, side, options = {}) {
  const box = el("div", null, "deck-box" + (side ? " " + side : ""));
  const list = el("ul", null, "deck");
  for (const card of deck.cards) list.append(cardTile(card));
  box.append(list);
  const s = deck.summary || {};
  const facts = el("p", null, "deck-facts");
  if (s.avg_elixir !== null && s.avg_elixir !== undefined) facts.append(fact(s.avg_elixir, "avg elixir"));
  if (s.cycle !== null && s.cycle !== undefined) facts.append(fact(s.cycle, "4-card cycle"));
  if (s.evolutions) facts.append(fact(s.evolutions, s.evolutions === 1 ? "evolution" : "evolutions"));
  if (s.heroes) facts.append(fact(s.heroes, s.heroes === 1 ? "hero" : "heroes"));
  if (s.either) facts.append(fact(s.either, "evo or hero"));
  if (s.fixed_levels) facts.append(el("span", "Levels set by the challenge"));
  else if (s.maxed !== null && s.maxed !== undefined) facts.append(fact(s.maxed + "/" + s.levels_known, "maxed"));
  if (facts.childElementCount) box.append(facts);
  if (side === "theirs" && options.read !== false) {
    const read = playAround(deck.cards);
    if (read) box.append(read);
  }
  if (s.hidden) box.append(el("p", "One card is hidden by the game's API; elixir counts the other seven.", "note"));
  if (s.either) box.append(el("p", "Evo or Hero: the API doesn't say which one was active.", "note"));
  return box;
}

const READ_KINDS = [["spell", "Spells"], ["building", "Buildings"]];

// What to play around: their spells and buildings, cheapest first.
function playAround(cards) {
  const rows = [];
  for (const [kind, title] of READ_KINDS) {
    const these = cards.filter((c) => c.kind === kind)
      .sort((a, b) => (a.elixir || 0) - (b.elixir || 0));
    if (!these.length) continue;
    const row = el("div", null, "row-of");
    row.append(el("span", title, "what"));
    for (const c of these) {
      const chip = el("span", null, "chip");
      if (c.elixir !== null && c.elixir !== undefined) chip.append(elixirDrop(c.elixir));
      chip.append(el("span", c.name));
      row.append(chip);
    }
    rows.push(row);
  }
  if (!rows.length) return null;
  const box = el("div", null, "play-around");
  box.append(el("p", "Play around", "label"), ...rows);
  return box;
}

// Card ids, then a "form token" for each active Evolution or Hero, as the server's
// models expect (battles.FORM_TOKEN: Evo Giant is not plain Giant).
const FORM_TOKEN = { evo: 1000000000, hero: 2000000000 };

// "Copy to game": a QR code to scan with your phone (the app is on your PC), the
// link itself, and a copy button. ids: card ids in slot order, then form tokens.
function copyDeckButton(ids) {
  const wrap = el("div", null, "copy-deck");
  const button = action("Copy to game", null);
  button.setAttribute("aria-expanded", "false");
  const panel = el("div", null, "copy-panel");
  panel.hidden = true;
  async function load() {
    show(panel, loadingText("Making the link…"));
    try {
      const r = await api("/api/deck-link", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ deck: ids }),
      });
      const copy = action("Copy link", async () => {
        try {
          await navigator.clipboard.writeText(r.url);
          copy.textContent = "Copied";
        } catch {
          copy.textContent = "Couldn't copy: use the link";
        }
      });
      const open = el("a", "Open link");
      open.href = r.url;
      open.target = "_blank";
      open.rel = "noopener noreferrer";
      const actions = el("p", null, "copy-actions");
      actions.append(copy, open);
      show(panel, qrCode(r.qr), el("p", "Scan with your phone's camera to open it in Clash Royale.", "note"), actions);
    } catch (error) {
      show(panel, errorBox(error, load));
    }
  }
  button.addEventListener("click", () => {
    const opening = panel.hidden;
    panel.hidden = !opening;
    button.setAttribute("aria-expanded", String(opening));
    if (opening && !panel.childElementCount) load();
  });
  wrap.append(button, panel);
  return wrap;
}

// Rows of 0/1 from the server, drawn as squares with a white quiet zone.
function qrCode(rows) {
  const n = rows.length, pad = 4;
  const code = svg("svg", { viewBox: "0 0 " + (n + 2 * pad) + " " + (n + 2 * pad), class: "qr",
    "shape-rendering": "crispEdges" });
  code.setAttribute("role", "img");
  code.setAttribute("aria-label", "QR code for this deck");
  code.append(svg("rect", { width: n + 2 * pad, height: n + 2 * pad, fill: "#fff" }));
  rows.forEach((row, y) => {
    for (let x = 0; x < row.length; x++) {
      if (row[x] === "1") code.append(svg("rect", { x: x + pad, y: y + pad, width: 1, height: 1, fill: "#000" }));
    }
  });
  return code;
}

function knownIds(deck) {
  const known = deck.cards.filter((c) => c.id !== null && c.id !== undefined);
  const forms = known.filter((c) => FORM_TOKEN[c.form]).map((c) => c.id + FORM_TOKEN[c.form]);
  return known.map((c) => c.id).concat(forms);
}

function seenText(minutes) {
  if (minutes < 2) return "online now";
  if (minutes < 60) return "seen " + minutes + " min ago";
  if (minutes < 48 * 60) return "seen " + Math.round(minutes / 60) + " h ago";
  return "seen " + Math.round(minutes / 1440) + " days ago";
}

// The facts about a player, each its own text node. Names and clans go in
// <bdi> so right-to-left text can't reorder the tag and numbers around it.
function playerFacts(player, options = {}) {
  const facts = [];
  if (options.withTag !== false && player.tag) facts.push(el("span", player.tag));
  if (player.clan) facts.push(el("bdi", player.clan));
  else if (options.showNoClan) facts.push(el("span", "no clan"));
  if (player.rank) facts.push(el("span", "rank #" + player.rank.toLocaleString()));
  if (player.rating) facts.push(el("span", "rating " + player.rating));
  else if (player.league) facts.push(el("span", "league " + player.league));
  if (player.trophies) facts.push(el("span", player.trophies.toLocaleString() + " trophies"));
  if (player.last_seen_minutes !== null && player.last_seen_minutes !== undefined) {
    facts.push(el("span", seenText(player.last_seen_minutes), player.last_seen_minutes < 2 ? "online" : undefined));
  }
  return facts;
}

function playerLine(player, options = {}) {
  const line = el("p", null, "player");
  line.append(el("bdi", player.name || "Unknown", "name"));
  const facts = el("span", null, "facts");
  facts.append(...playerFacts(player, options));
  line.append(facts);
  return line;
}

// --- matchups -----------------------------------------------------------------

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

// A thin blue/red bar: blue fills your win rate (the score bug in small).
function winBar(rate) {
  const bar = el("span", null, "bar" + (rate === null ? " none" : ""));
  bar.setAttribute("aria-hidden", "true");
  if (rate !== null) bar.style.setProperty("--p", (rate * 100).toFixed(1) + "%");
  return bar;
}

function recordCell(cell) {
  const td = el("td", null, "record");
  if (cell.mirror) {
    td.append(el("span", "Mirror", "rate"), el("span", plural(cell.games, "game", "games"), "games"));
  } else if (cell.games) {
    const lean = cell.range[0] > 0.5 ? " good" : cell.range[1] < 0.5 ? " bad" : "";
    td.append(el("span", pct(cell.rate), "rate" + lean), winBar(cell.rate));
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
  const table = el("table", null, "tiers matchup-record");
  table.append(el("thead"), el("tbody"));
  const head = el("tr");
  head.append(el("th", "Decks alike"));
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
  const table = el("table", null, "tiers matchup-record");
  table.append(el("thead"), el("tbody"));
  const head = el("tr");
  head.append(el("th", "Decks alike"), el("th", "All stored battles"));
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

// The odds as a score bug: blue fills the bar by your win chance.
function oddsBug(m) {
  const you = m.mirror ? 0.5 : m.win_chance;
  const box = el("div", null, "odds" + (m.mirror ? " mirror" : ""));
  const numbers = el("div", null, "odds-numbers");
  numbers.setAttribute("aria-hidden", "true");  // the sentence below says the same
  const a = el("span", pct(you), "you");
  a.append(el("small", "you"));
  const b = el("span", pct(1 - you), "them");
  b.append(el("small", "them"));
  numbers.append(a, b);
  const names = el("p", null, "archetypes");
  names.append(el("span", m.archetype_a, "a"), el("span", m.archetype_b, "b"));
  const said = m.mirror ? "Mirror match: a coin flip on decks."
    : "Your deck wins about " + pct(you) + " of games like this.";
  const head = el("div", null, "odds-head");
  head.append(el("p", "Odds", "label"));
  if (m.based_on) head.append(el("span", plural(m.based_on.games, "game", "games"), "basis"));
  else if (m.mirror) head.append(el("span", "Mirror match", "basis"));
  box.append(head, el("p", said, "visually-hidden"), numbers, names);
  // Blue fills exactly your win chance, sliding from level to the result.
  requestAnimationFrame(() => requestAnimationFrame(() => {
    numbers.style.setProperty("--p", (you * 100).toFixed(1) + "%");
  }));
  return box;
}

// Everything behind the odds: the record table, notes, the cards that decide
// it, and the share image. decks: { mine, theirs } for the share image.
function matchupDetail(m, mode, decks) {
  const box = el("div", null, "matchup");
  box.append(el("p", "How the matchup has gone", "label"));
  if (m.breakdown) {
    box.append(breakdownTable(m.breakdown), disclosure("How to read this",
      el("p", "Your win rate in Ranked games over the last " + m.breakdown.window_days + " days. " +
        "Exact: both decks card for card. 7+ / 6+ of 8: each deck shares at least that many cards " +
        "with yours and theirs, keeping the same win condition and spells. Archetype: same deck " +
        "styles. Top 200 / 1,000 count only games where both players were in that bracket.", "note")));
  } else if (m.record || m.mirror) {
    box.append(recordOnly(m));
  }
  const about = [];
  if (m.based_on) {
    about.push(el("p", "Odds come from " + plural(m.based_on.games, "game", "games") + " between " +
      m.based_on.words + ", pulled toward " + (m.estimate === "model" ? "the model's" : "the archetype") +
      " estimate of " + pct(m.estimate_chance) + " while there are few.", "note"));
  }
  if (m.note) about.push(el("p", m.note, "note"));
  if (about.length) box.append(disclosure("About these odds", ...about));
  if (mode === "challenge" || mode === "trophy") {
    box.append(el("p", "From Ranked games, so a rough guide for " +
      (mode === "challenge" ? "the challenge." : "Trophy Road, where card levels matter more."), "note"));
  }
  if (m.key_cards && m.key_cards.length) {
    const cards = el("ul", null, "key-cards");
    for (const k of m.key_cards) {
      const item = el("li");
      item.append(el("span", k.card, k.effect > 0 ? "yours" : "theirs"),
        k.effect > 0 ? " in your deck helps you" : " in their deck helps them");
      cards.append(item);
    }
    box.append(el("p", "Cards that decide it", "label"), cards);
  }
  if (decks && decks.mine && decks.theirs) box.append(shareButton(m, decks.mine, decks.theirs));
  return box;
}

// --- share image ----------------------------------------------------------------

const SHARE = { w: 1080, h: 1440, bg: "#0a0e18", panel: "#111727", text: "#f1f4fb",
  muted: "#a7b1cc", blue: "#2f62e8", blueText: "#7aa2ff", red: "#d62f3b", redText: "#ff7a82",
  elixir: "#9b2fc9" };

function loadImage(src) {
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => resolve(null);  // no art: the card's name is drawn instead
    img.src = src;
  });
}

function cardSrc(card) {
  const form = card.form === "evo" || card.form === "hero" ? "?form=" + card.form : "";
  return "/cards/" + encodeURIComponent(card.id) + ".png" + form;
}

// One deck: 4 x 2 cards on a band in its team colour, with its archetype above.
async function drawDeck(ctx, deck, x, y, colour, title) {
  const C = SHARE, cw = 180, ch = 216, gap = 16;
  ctx.fillStyle = colour;
  ctx.fillRect(x - 16, y, 4 * cw + 3 * gap + 32, 60);
  ctx.fillStyle = "#fff";
  ctx.font = "700 44px 'Barlow Condensed', sans-serif";
  ctx.fillText(title.toUpperCase(), x, y + 45);
  ctx.fillStyle = C.panel;
  ctx.fillRect(x - 16, y + 60, 4 * cw + 3 * gap + 32, 2 * ch + gap + 32);
  const images = await Promise.all(deck.cards.map((c) => (c.id ? loadImage(cardSrc(c)) : null)));
  deck.cards.slice(0, 8).forEach((card, i) => {
    const cx = x + (i % 4) * (cw + gap), cy = y + 76 + Math.floor(i / 4) * (ch + gap);
    if (images[i]) {
      ctx.drawImage(images[i], cx, cy, cw, ch);
    } else {
      ctx.strokeStyle = C.muted;
      ctx.setLineDash([10, 8]);
      ctx.strokeRect(cx + 4, cy + 4, cw - 8, ch - 8);
      ctx.setLineDash([]);
      ctx.fillStyle = C.muted;
      ctx.font = "600 28px system-ui, sans-serif";
      ctx.fillText(card.name || "?", cx + 16, cy + ch / 2, cw - 32);
    }
    if (card.elixir) {
      ctx.fillStyle = C.elixir;
      ctx.beginPath();
      ctx.arc(cx + 30, cy + 30, 24, 0, 2 * Math.PI);
      ctx.fill();
      ctx.fillStyle = "#fff";
      ctx.font = "700 32px 'Barlow Condensed', sans-serif";
      ctx.textAlign = "center";
      ctx.fillText(String(card.elixir), cx + 30, cy + 41);
      ctx.textAlign = "left";
    }
  });
}

async function shareCanvas(m, mine, theirs) {
  const C = SHARE;
  try { await document.fonts.load("700 64px 'Barlow Condensed'"); } catch { /* system font */ }
  const canvas = document.createElement("canvas");
  canvas.width = C.w;
  canvas.height = C.h;
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = C.bg;
  ctx.fillRect(0, 0, C.w, C.h);
  const left = (C.w - (4 * 180 + 3 * 16)) / 2;   // the decks sit centred
  await drawDeck(ctx, mine, left, 30, C.blue, m.archetype_a);
  // The verdict between the decks: the odds and the split bar.
  const you = m.mirror ? 0.5 : m.win_chance, top = 590;
  ctx.font = "700 120px 'Barlow Condensed', sans-serif";
  ctx.fillStyle = C.blueText;
  ctx.fillText(pct(you), 60, top + 100);
  ctx.fillStyle = C.redText;
  ctx.textAlign = "right";
  ctx.fillText(pct(1 - you), C.w - 60, top + 100);
  ctx.textAlign = "left";
  ctx.fillStyle = C.red;
  ctx.fillRect(60, top + 124, C.w - 120, 28);
  ctx.fillStyle = C.blue;
  ctx.fillRect(60, top + 124, (C.w - 120) * you, 28);
  ctx.fillStyle = C.text;
  ctx.fillRect(C.w / 2 - 2, top + 116, 4, 44);
  await drawDeck(ctx, theirs, left, top + 180, C.red, m.archetype_b);
  ctx.fillStyle = C.muted;
  ctx.font = "500 26px system-ui, sans-serif";
  const basis = m.based_on ? "From " + plural(m.based_on.games, "game", "games") + " between " +
    m.based_on.words + "." : "Model estimate for equally skilled players.";
  ctx.fillText("DeckLens · " + basis, 60, C.h - 56);
  ctx.font = "400 20px system-ui, sans-serif";
  ctx.fillText("Not affiliated with or endorsed by Supercell.", 60, C.h - 24);
  return canvas;
}

function shareButton(m, mine, theirs) {
  const wrap = el("div", null, "share");
  const button = action("Share image", null);
  button.setAttribute("aria-expanded", "false");
  const panel = el("div", null, "share-panel");
  panel.hidden = true;
  button.addEventListener("click", async () => {
    panel.hidden = !panel.hidden;
    button.setAttribute("aria-expanded", String(!panel.hidden));
    if (panel.hidden || panel.childElementCount) return;
    show(panel, loadingText("Drawing…"));
    const canvas = await shareCanvas(m, mine, theirs);
    canvas.className = "share-preview";
    canvas.setAttribute("role", "img");
    canvas.setAttribute("aria-label", "Share image: " + m.archetype_a + " vs " + m.archetype_b +
      ", " + pct(m.win_chance) + " to " + pct(1 - m.win_chance));
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
    const save = el("a", "Download PNG", "quiet");
    save.href = URL.createObjectURL(blob);
    save.download = "decklens-" + (m.archetype_a + "-vs-" + m.archetype_b).toLowerCase()
      .replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") + ".png";
    const copy = action("Copy image", async () => {
      try {
        await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
        copy.textContent = "Copied";
      } catch {
        copy.textContent = "Couldn't copy: download it";
      }
    });
    const actions = el("p", null, "copy-actions");
    actions.append(save, copy);
    show(panel, canvas, actions);
  });
  wrap.append(button, panel);
  return wrap;
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
  show(out, loadingText("Looking up…"));
  try {
    const me = await api("/api/players/" + tagPath(tag));
    state.me = me;
    state.myDecks = undefined;  // your Trophy Road / challenge decks: fetched again for this player
    storageSet("decklens.myTag", me.tag);
    show(out, youView(me));
  } catch (error) {
    state.me = null;
    state.myDecks = undefined;
    show(out, errorBox(error, () => showMe(tag)));
  }
  if (board.key === null) boardIdle();  // the board's blue side is you
  else refreshYouSide();
}

// Wide screens: you and your deck on the left, today / record / rank / tweaks
// on the right. Narrow screens stack them.
function youView(me) {
  const grid = el("div", null, "you-grid");
  const left = el("div");
  const head = el("div", null, "you-head");
  head.append(el("bdi", me.name || "You", "name"));
  const facts = el("span", null, "facts");
  facts.append(...playerFacts(me));
  head.append(facts);
  const deck = el("div", null, "you-deck");
  deck.append(el("p", "Current deck", "label"), me.deck.cards.length ? deckView(me.deck, "yours")
    : el("p", "No current deck in your profile.", "muted"));
  if (me.deck.cards.length && knownIds(me.deck).length >= 8) deck.append(copyDeckButton(knownIds(me.deck)));
  left.append(head, deck);
  const right = el("div");
  right.append(...youTabs(me));
  grid.append(left, right);
  return grid;
}

// Today, record, rank and tweaks share one spot. A tab is built the first time
// it's opened; the last one you used is remembered.
const YOU_TABS = [["today", "Today"], ["record", "Record"], ["rank", "Rank"], ["tweaks", "Tweaks"]];

function youTabs(me) {
  const bar = el("div", null, "subtabs");
  bar.setAttribute("role", "tablist");
  bar.setAttribute("aria-label", "About you");
  const panel = el("div", null, "tab-panel");
  panel.setAttribute("role", "tabpanel");
  panel.id = "you-panel";
  const built = {};
  const buttons = {};

  function build(key) {
    const box = el("div");
    if (key === "record") {
      showRecord(me.tag, box, 30);
    } else if (key === "today") {
      showSession(me.tag, box);
    } else if (key === "rank") {
      showRank(me.tag, box, 30);
    } else if (me.deck.cards.length) {
      showTweaks(me.deck, box, "all");
    } else {
      box.append(el("p", "No current deck to tweak.", "muted"));
    }
    return box;
  }

  function select(key) {
    for (const [k] of YOU_TABS) selectTab(buttons[k], k === key);
    panel.setAttribute("aria-labelledby", buttons[key].id);
    built[key] = built[key] || build(key);
    panel.replaceChildren(built[key]);
    storageSet("decklens.youTab", key);
  }

  for (const [key, label] of YOU_TABS) {
    const b = el("button", label, "tab");
    b.type = "button";
    b.id = "you-tab-" + key;
    b.setAttribute("role", "tab");
    b.setAttribute("aria-controls", panel.id);
    b.addEventListener("click", () => select(key));
    buttons[key] = b;
    bar.append(b);
  }
  const saved = storageGet("decklens.youTab");
  select(YOU_TABS.some(([k]) => k === saved) ? saved : "today");
  return [bar, panel];
}

// --- today's session ------------------------------------------------------------

function refreshButton(onClick) {
  return action("Refresh", onClick);
}

const STREAK_WORDS = { won: ["Won", "Last game: won"], lost: ["Lost", "Last game: lost"], draw: ["Drew", "Last game: a draw"] };

async function showSession(tag, box) {
  show(box, loadingText("Reading your battle log…"));
  let s;
  try {
    s = await api("/api/players/" + tagPath(tag) + "/session");
  } catch (error) {
    show(box, errorBox(error, () => showSession(tag, box)));
    return;
  }
  if (!s.games) {
    show(box, calm("No Ranked games in your recent battle log.", null,
      refreshButton(() => showSession(tag, box))));
    return;
  }
  const score = el("p", null, "session-score");
  score.setAttribute("aria-label", s.wins + " wins, " + s.losses + " losses");
  score.append(el("span", String(s.wins), "result-won"), el("span", "–"),
    el("span", String(s.losses), "result-lost"));
  const parts = [score, el("p", pct(s.wins / Math.max(1, s.wins + s.losses)) + " won · " +
    plural(s.games, "game", "games") + " since " + new Date(s.started).toLocaleTimeString([],
      { hour: "numeric", minute: "2-digit" }), "detail")];
  // Oldest on the left, like reading the session from the start.
  const strip = el("ol", null, "session-strip");
  strip.setAttribute("aria-label", "Results, oldest first");
  for (const r of [...s.results].reverse()) {
    strip.append(el("li", r === "won" ? "W" : r === "lost" ? "L" : "D", "dot-" + r));
  }
  parts.push(strip);
  const st = s.streak;
  const words = STREAK_WORDS[st.result] || STREAK_WORDS.draw;
  parts.push(el("p", st.games === 1 ? words[1] + "." : words[0] + " " + st.games + " in a row.", "detail"));
  if (s.rank && s.rank.now) {
    const r = s.rank;
    let text = "Rank now #" + r.now.toLocaleString();
    if (r.start) {
      const moved = r.start - r.now;
      text += moved ? " (" + (moved > 0 ? "up " : "down ") + Math.abs(moved).toLocaleString() +
        " from #" + r.start.toLocaleString() + ")" : " (no change)";
    }
    parts.push(el("p", text, "detail"));
  }
  if (s.tilt) {
    parts.push(el("p", plural(st.games, "loss", "losses") + " in a row. A short break usually " +
      "does more for your rank than the next game.", "tilt"));
  }
  parts.push(refreshButton(() => showSession(tag, box)));
  show(box, ...parts);
}

// --- rank graph ----------------------------------------------------------------

const RANK_DAYS = [7, 30, 90];

function filterRow(label, options, chosen, onPick) {
  const row = el("div", null, "filters");
  row.setAttribute("role", "group");
  row.setAttribute("aria-label", label);
  for (const [value, text] of options) {
    const b = action(text, () => onPick(value), "filter");
    b.setAttribute("aria-pressed", String(value === chosen));
    row.append(b);
  }
  return row;
}

// Rank over time, best (lowest number) at the top.
function rankChart(points) {
  const W = 600, H = 200, PAD = 8;
  const times = points.map((p) => new Date(p.time).getTime());
  const ranks = points.map((p) => p.rank);
  const t0 = Math.min(...times), t1 = Math.max(...times);
  const best = Math.min(...ranks), worst = Math.max(...ranks);
  const x = (t) => PAD + (t1 > t0 ? (t - t0) / (t1 - t0) : 0.5) * (W - 2 * PAD);
  const y = (r) => PAD + (worst > best ? (r - best) / (worst - best) : 0.5) * (H - 2 * PAD);
  const chart = svg("svg", { viewBox: "0 0 " + W + " " + H, class: "rank-chart",
    preserveAspectRatio: "none" });
  chart.setAttribute("role", "img");
  chart.setAttribute("aria-label", "Rank from #" + ranks[0].toLocaleString() + " to #" +
    ranks[ranks.length - 1].toLocaleString() + ", best #" + best.toLocaleString());
  chart.append(svg("polyline", { points: points.map((p, i) => x(times[i]) + "," + y(p.rank)).join(" "),
    class: "rank-line", "vector-effect": "non-scaling-stroke" }));
  chart.append(svg("circle", { cx: x(times[times.length - 1]), cy: y(ranks[ranks.length - 1]), r: 4,
    class: "rank-now" }));
  return { chart, best, worst };
}

async function showRank(tag, box, days) {
  const filters = filterRow("How far back", RANK_DAYS.map((d) => [d, d + " days"]), days,
    (d) => showRank(tag, box, d));
  show(box, filters, loadingText("Loading your rank…"));
  let h;
  try {
    h = await api("/api/players/" + tagPath(tag) + "/rank-history?days=" + days);
  } catch (error) {
    show(box, filters, errorBox(error, () => showRank(tag, box, days)));
    return;
  }
  const pts = h.points;
  if (!pts.length) {
    show(box, filters, calm("No rank yet this season.", " A global rank starts at about the top 10,000."));
    return;
  }
  const now = pts[pts.length - 1].rank;
  const head = el("p", null, "session-score");
  head.append(el("span", "#" + now.toLocaleString()));
  const parts = [filters, head];
  if (pts.length > 1) {
    const { chart, best, worst } = rankChart(pts);
    const wrap = el("div", null, "rank-wrap");
    const scale = el("div", null, "rank-scale");
    scale.setAttribute("aria-hidden", "true");
    scale.append(el("span", "#" + best.toLocaleString()), el("span", "#" + worst.toLocaleString()));
    const axis = el("p", null, "rank-axis");
    axis.append(el("span", new Date(pts[0].time).toLocaleDateString()), el("span", "now"));
    wrap.append(scale, chart, axis);
    parts.push(wrap);
  } else {
    parts.push(calm("One reading so far.", " The graph fills in as your games are collected."),
      disclosure("Setup", el("p", "Rank readings are saved every 15 minutes while DeckLens is open, " +
        "for the tags listed in DECKLENS_MY_TAGS.", "setup")));
  }
  show(box, ...parts);
}

// --- deck tweak finder ---------------------------------------------------------

function signed(g) {
  const pts = Math.round(g * 1000) / 10;
  return (pts > 0 ? "+" : pts < 0 ? "−" : "±") + Math.abs(pts).toFixed(1) + "%";
}

// Your deck's ids with one card swapped (the new card's Evo/Hero form comes along).
function swappedIds(deck, s) {
  const FORM = 1000000000;
  const newBase = s.in_id % FORM;
  const ids = knownIds(deck)
    .filter((c) => !(c >= FORM && c % FORM === s.out_id))   // the old card's Evo/Hero goes too
    .map((c) => (c === s.out_id ? newBase : c));
  if (s.in_id >= FORM) ids.push(s.in_id);
  return ids;
}

function gainCell(c) {
  const td = el("td", null, "record");
  if (!c || !c.games) {
    td.append(el("span", "—", "rate"), el("span", "0 games", "games"));
    return td;
  }
  td.append(el("span", signed(c.gain), "rate" + (c.gain >= 0.005 ? " good" : c.gain <= -0.005 ? " bad" : "")));
  const games = el("span", plural(c.games, "game", "games"), "games");
  if (c.games < 30) games.append(el("span", " (few)", "few"));
  td.append(games, el("span", pct(c.rate) + " won", "range"));
  return td;
}

// One-card swaps real players of your deck make, and how they did, by rank range.
async function showTweaks(deck, box, band) {
  show(box, loadingText("Looking at decks one card away from yours…"));
  let t;
  try {
    t = await api("/api/tweaks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ deck: knownIds(deck), band }),
    });
  } catch (error) {
    show(box, errorBox(error, () => showTweaks(deck, box, band)));
    return;
  }
  const bands = t.bands.filter((b) => b.base !== undefined);
  if (!t.swaps.length || !bands.length) {
    show(box, calm("No one-card swaps of your deck with 10+ stored games" +
      (band === "all" ? "" : " in this range") + " yet.", " Try another range, or check back after more crawls."));
    return;
  }
  const sorter = filterRow("Sort by rank range", bands.map((b) => [b.key, b.label]), band,
    (key) => showTweaks(deck, box, key));
  const wrap = el("div", null, "table-wrap");
  const table = el("table", null, "tiers tweak-table");
  table.append(el("thead"), el("tbody"));
  const head = el("tr");
  head.append(el("th", "Swap"));
  for (const b of bands) head.append(el("th", b.label));
  const now = el("tr", null, "now");
  now.append(el("th", "Your deck now"));
  for (const b of bands) {
    const td = el("td", null, "record");
    td.append(el("span", pct(b.base), "rate"), el("span", plural(b.games, "game", "games"), "games"));
    now.append(td);
  }
  table.tHead.append(head);
  table.tBodies[0].append(now);
  for (const s of t.swaps) {
    const tr = el("tr");
    const swap = el("th");
    swap.append(el("span", s.out, "swap-out"), el("span", " → "), el("span", s.in, "swap-in"));
    swap.append(copyDeckButton(swappedIds(deck, s)));
    tr.append(swap);
    for (const b of bands) tr.append(gainCell(s.bands[b.key]));
    table.tBodies[0].append(tr);
  }
  wrap.append(table);
  show(box, sorter, wrap, disclosure("How swaps are scored",
    el("p", "Stored Ranked games of decks sharing 7 of your 8 cards: the swap is the card they play " +
      "instead of one of yours. Win rates are pulled toward all decks close to yours (6+ of 8) by " +
      "20 games, so a lucky 3–0 doesn't top the list. Rank ranges use each player's global rank when " +
      "they played; swaps need 10+ games in the range you sort by.", "note")));
}

const RECORD_DAYS = [7, 30, 90];

// Eight small card pictures in a row: a deck you can take in at a glance.
function miniDeck(deck) {
  const list = el("ul", null, "mini-deck");
  for (const card of deck.cards) {
    const item = el("li");
    if (card.hidden || card.id === null || card.id === undefined) {
      item.append(el("span", "?", "mini-missing"));
    } else {
      const img = document.createElement("img");
      const form = card.form === "evo" || card.form === "hero" ? "?form=" + card.form : "";
      img.src = "/cards/" + encodeURIComponent(card.id) + ".png" + form;
      img.alt = card.name + (FORM_LABELS[card.form] ? " (" + FORM_LABELS[card.form] + ")" : "");
      img.title = img.alt;
      img.width = 40;
      img.height = 48;
      img.loading = "lazy";
      img.addEventListener("error", () => img.replaceWith(el("span", card.name, "mini-name")));
      item.append(img);
    }
    list.append(item);
  }
  return list;
}

// Your latest games against one archetype, shown when you open its row.
function recordDetail(a) {
  const box = el("div");
  if (!a.recent || !a.recent.length) {
    box.append(el("p", "No game details stored.", "muted"));
    return box;
  }
  for (const g of a.recent) {
    const game = el("div", null, "record-game");
    const line = el("p", null, "meeting");
    line.append(el("span", RESULT_WORDS[g.result], "result-" + g.result), el("span", ago(g.time), "muted"));
    game.append(line, miniDeck(g.deck));
    box.append(game);
  }
  return box;
}

function total(variants) {
  return variants.reduce((n, a) => n + a.games, 0);
}

// A row you can open: name, wins–losses and win rate; opening it shows detail()
// underneath (built once). One open row per list.
function expandRow(list, name, wins, games, range, detail, extraClass) {
  const item = el("li");
  const row = el("button", null, "record-row" + (extraClass ? " " + extraClass : ""));
  row.type = "button";
  row.setAttribute("aria-expanded", "false");
  const rate = wins / games;
  const lean = range ? (range[0] > 0.5 ? " good" : range[1] < 0.5 ? " bad" : "")
    : (rate >= 0.6 ? " good" : rate <= 0.4 ? " bad" : "");
  row.append(el("span", name, "row-name"), el("span", wins + "–" + (games - wins), "row-record"),
    el("span", pct(rate), "row-rate rate" + lean));
  if (games < 10) row.title = "Few games, so the win rate can swing a lot";
  let box = null;
  item.open = (opening, exclusive = true) => {  // a search opens every match at once
    if (opening && exclusive && list.openItem && list.openItem !== item) list.openItem.open(false);
    if (opening && !box) {
      box = el("div", null, "record-detail");
      box.append(detail());
      item.append(box);
    }
    if (box) box.hidden = !opening;
    row.setAttribute("aria-expanded", String(opening));
    if (opening && exclusive) list.openItem = item;
    else if (list.openItem === item) list.openItem = null;
  };
  row.addEventListener("click", () => item.open(row.getAttribute("aria-expanded") !== "true"));
  item.append(row);
  return item;
}

// Words a row can be found by: the archetype, plus every card the opponents played.
function recordWords(a) {
  const cards = (a.recent || []).flatMap((g) => g.deck.cards.map((c) => c.name));
  return [a.archetype, ...cards].join(" ").toLowerCase();
}

// Your own Ranked record against each archetype you've faced: type to filter,
// open a row to see your latest games against it.
async function showRecord(tag, box, days) {
  const filters = filterRow("How far back", RECORD_DAYS.map((d) => [d, d + " days"]), days,
    (d) => showRecord(tag, box, d));
  show(box, filters, loadingText("Loading your games…"));
  let r;
  try {
    r = await api("/api/players/" + tagPath(tag) + "/record?days=" + days);
  } catch (error) {
    show(box, filters, errorBox(error, () => showRecord(tag, box, days)));
    return;
  }
  const parts = [filters];
  if (!r.games) {
    parts.push(calm("No Ranked games in this window yet."));
  } else {
    parts.push(el("p", "Ranked: " + r.wins + " wins from " + plural(r.games, "game", "games") +
      " (" + pct(r.wins / r.games) + ").", "detail"));
    const search = el("input", null, "record-search");
    search.type = "search";
    search.placeholder = "Find a win condition or card, e.g. golem";
    search.setAttribute("aria-label", "Find a win condition, deck or card");
    search.maxLength = 40;
    const none = el("p", "Nothing matches that.", "muted");
    none.hidden = true;
    // Win condition first ("Golem"), then the versions of it you've faced ("Golem Pump").
    const families = new Map();
    for (const a of r.archetypes) {
      if (!families.has(a.family)) families.set(a.family, []);
      families.get(a.family).push(a);
    }
    const list = el("ul", null, "record-list");
    const familyItems = [];
    for (const [family, variants] of [...families].sort((x, y) => total(y[1]) - total(x[1]))) {
      const wins = variants.reduce((n, a) => n + a.wins, 0);
      const games = total(variants);
      const inner = el("ul", null, "record-list variants");
      for (const a of variants) {
        const item = expandRow(inner, a.archetype, a.wins, a.games, a.range, () => recordDetail(a));
        item.dataset.words = recordWords(a) + " " + family.toLowerCase();
        inner.append(item);
      }
      const item = expandRow(list, family, wins, games, null, () => inner, "family-row");
      list.append(item);
      familyItems.push([item, inner]);
    }
    search.addEventListener("input", () => {
      const words = search.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
      let shown = 0;
      for (const [item, inner] of familyItems) {
        let any = false;
        for (const v of inner.children) {
          v.hidden = !words.every((w) => v.dataset.words.includes(w));
          any = any || !v.hidden;
        }
        item.hidden = !any;
        if (any) shown++;
        if (words.length) item.open(any, false);   // searching: show the matching versions
      }
      none.hidden = shown > 0;
    });
    parts.push(search, list, none);
  }
  if (!r.stored) {
    parts.push(disclosure("Keep a longer record", el("p", "Only your last ~25 battles count right now. " +
      "Add your tag to DECKLENS_MY_TAGS in .env and your games are saved every 15 minutes while " +
      "DeckLens is open, and with each scheduled crawl.", "setup")));
  }
  show(box, ...parts);
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
    else show(out, errorBox(new Error("That isn't a valid player tag: tags use only 0289PYLQGRJCUV.")));
    return;
  }
  show(out, loadingText("Searching…"));
  let results = [];
  let searchError = null;
  try {
    results = (await api("/api/search?name=" + encodeURIComponent(text))).results;
  } catch (error) {
    searchError = error;
  }
  if (!results.length && !tag) {
    show(out, searchError ? errorBox(searchError, lookUpMe) : calm("No one by that name yet.",
      " Try your player tag: it's under your name in your profile."));
    return;
  }
  const list = el("ul", null, "results");
  if (tag) list.append(pickButton(el("p", "Use player tag " + tag, "player"), () => showMe(tag)));
  for (const r of results) list.append(pickButton(playerLine(r), () => showMe(r.tag)));
  show(out, el("p", "Which one is you?", "label"), list);
}

// --- the battle board -----------------------------------------------------------
// The top of Scout: a versus bar (you in blue, them in red), their deck, what to
// play around and the odds. Every way of finding an opponent ends here.

const board = { token: 0, key: null };

function boardSay(text) {
  document.getElementById("board-said").textContent = text;
}

// tools: what sits on the strip above the VS bar (game modes, Wrong player?).
function setBoard(...children) {
  show(document.getElementById("board-body"), ...children);
  show(document.getElementById("board-tools"));
}

function setTools(...nodes) {
  show(document.getElementById("board-tools"), ...nodes);
}

// tag: { text, kind } on their side: "sure" (yellow), "doubt" (outlined) or plain.
function youSide() {
  const you = el("div", null, "vs-side vs-you");
  you.append(el("bdi", state.me ? state.me.name || "You" : "You", "vs-name"));
  const facts = el("div", null, "vs-facts");
  if (state.me) {
    const s = state.me.deck && state.me.deck.summary;
    if (s && s.avg_elixir !== null && s.avg_elixir !== undefined) facts.append(el("span", s.avg_elixir + " avg elixir"));
    facts.append(...playerFacts(state.me, { withTag: false }));
    you.append(facts);
  } else {
    you.append(action("Set your tag", goToYou, "quiet on-band"));
  }
  return you;
}

// Looked up after the board was drawn: put you on it.
function refreshYouSide() {
  const old = document.querySelector("#board-body .vs-you");
  if (old) old.replaceWith(youSide());
}

function vsBar(them, tag, lands) {
  const bar = el("div", null, "vs" + (lands ? " lands" : ""));
  const you = youSide();
  const mark = el("span", "VS", "vs-mark");
  mark.setAttribute("aria-hidden", "true");
  const side = el("div", null, "vs-side vs-them" + (them ? "" : " unknown"));
  if (tag) side.append(el("span", tag.text, "tag " + (tag.kind || "")));
  side.append(el("bdi", them ? them.name || "Name unreadable" : "No opponent yet", "vs-name"));
  if (them) {
    const facts = el("div", null, "vs-facts");
    facts.append(...playerFacts(them));
    side.append(facts);
  }
  bar.append(you, mark, side);
  return bar;
}

// The other candidates are in the list under the form: one click away.
function wrongPlayer() {
  const listed = document.querySelectorAll("#find-result .result-button").length > 1;
  return action(listed ? "Wrong player?" : "Find someone else", () => {
    const target = listed ? document.getElementById("find-result") : document.getElementById("find-name");
    target.scrollIntoView({ block: "start" });
    if (!listed) target.focus();
  }, "quiet wrong-player");
}

function goToYou() {
  setPage("you");
  document.getElementById("me-tag").focus();
}

function findByName() {
  document.getElementById("find-name").focus();
}

// Nobody on the board yet: what DeckLens is doing, and the ways to start.
function boardIdle() {
  board.token++;
  board.key = null;
  const watching = document.getElementById("auto-scout").checked;
  const idle = el("div", null, "board-idle");
  if (!state.me) {
    idle.append(el("p", "Set your player tag to scout", "big"),
      el("p", "Your tag finds your last opponent and works out the odds.", "muted"));
    const row = el("div", null, "board-actions");
    row.append(action("Go to You", goToYou, "primary"), action("Find by name", findByName));
    idle.append(row);
  } else {
    if (watching) idle.append(el("p", "Watching your phone for a battle", "big"));
    else idle.append(el("p", "Switch on auto-scout to find them as a battle starts.", "muted"));
    const row = el("div", null, "board-actions");
    row.append(action("Scout last opponent", scoutLast, "primary them"), action("Find by name", findByName));
    idle.append(row);
  }
  setBoard(vsBar(null, null, false), idle);
}

function boardFinding(read) {
  board.token++;
  board.key = "read:" + (read.name || "") + "|" + (read.clan || "");
  setBoard(vsBar({ name: read.name || "Name unreadable", clan: read.clan, rating: read.rating },
    { text: "Read from your phone", kind: "doubt" }, true), loadingText("Finding them…"));
  boardSay("In a battle. Looking for " + (read.name || "their clan") + ".");
}

// The read didn't match anyone: say so, and offer to read it again.
function boardNotFound(read, count) {
  const them = { name: read.name || "Name unreadable", clan: read.clan, rating: read.rating };
  const who = read.name ? "“" + read.name + "”" : "anyone in “" + read.clan + "”";
  const actions = [action("Read again", rescanOpponent, "primary them")];
  if (count) actions.push(action("Pick from the list", () => document.getElementById("find-result").scrollIntoView()));
  else actions.push(action("Search by hand", findByName));
  setBoard(vsBar(them, { text: "Not found", kind: "doubt" }, false),
    calm("Couldn't find " + who + (count ? " for sure." : "."),
      count ? " Closest matches are below." : " If the name was misread, read it again.", ...actions));
  boardSay("Couldn't find " + who + ".");
}

function boardUnreadable() {
  board.token++;
  board.key = "unreadable";
  const text = state.me ? "Their deck shows here when the battle ends."
    : "Set your player tag on You, and their deck shows here after the battle.";
  setBoard(vsBar({ name: "Name unreadable" }, { text: "Can't read their name", kind: "doubt" }, true),
    calm(text, " The name uses letters the screen reader doesn't know, like Arabic or emoji."));
  boardSay("In a battle, but their name can't be read.");
}

function newestPlay(decks) {
  return decks.reduce((best, d) => (String(d.last_played) > best ? String(d.last_played) : best), "");
}

// The odds for their deck: needs your deck too.
async function predict(mine, theirs) {
  if (!mine || knownIds(mine.deck).length < 7 || knownIds(theirs).length < 7) return null;
  try {
    return {
      m: await api("/api/predict", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ deck_a: knownIds(mine.deck), deck_b: knownIds(theirs) }),
      }),
    };
  } catch (error) {
    return { error };
  }
}

function oddsPanel(result, mine) {
  if (!mine) {
    return calm("Odds need your deck.", " Set your player tag on You.", action("Go to You", goToYou));
  }
  if (!result) return calm("Not enough of the decks is known for odds.");
  if (result.error) return errorBox(result.error);
  return oddsBug(result.m);
}

// Their deck large on the left; play around, odds, history and Copy to game
// on the right; your deck and the matchup detail under the fold.
function boardBody(deck, sub, mode, mine, result, history, extra) {
  const grid = el("div", null, "board-grid");
  const left = el("div");
  const label = el("p", "Their deck", "label");
  if (sub) label.append(el("span", sub, "sub"));
  left.append(label, deckView(deck, "theirs", { read: false }));
  const side = el("div", null, "board-side");
  const read = playAround(deck.cards);
  if (read) side.append(read);
  side.append(oddsPanel(result, mine));
  if (history) side.append(history);
  const actions = el("div", null, "board-actions");
  if (knownIds(deck).length >= 8) actions.append(copyDeckButton(knownIds(deck)));
  if (extra) actions.append(...extra);
  side.append(actions);
  grid.append(left, side);
  const parts = [grid];
  if (mine) {
    const more = el("div", null, "board-more two");
    const yours = el("div");
    yours.append(el("p", mine.label, "label"), deckView(mine.deck, "yours"));
    more.append(yours);
    if (result && result.m) more.append(matchupDetail(result.m, mode, { mine: mine.deck, theirs: deck }));
    parts.push(more);
  }
  return parts;
}

// "Played before": your earlier games against this player, filled in when it arrives.
async function fillHistory(player, slot) {
  if (!state.me || !player.tag || player.tag === state.me.tag) return;
  let found;
  try {
    found = await api("/api/players/" + tagPath(state.me.tag) + "/vs/" + tagPath(player.tag));
  } catch {
    return;  // optional extra: the board works without it
  }
  if (!found.meetings.length) return;
  const wins = found.meetings.filter((m) => m.result === "won").length;
  const losses = found.meetings.filter((m) => m.result === "lost").length;
  const last = found.meetings[0];
  const line = el("p", null, "history");
  const score = el("span", null, "score");
  score.append(el("span", String(wins), "result-won"), el("span", "–"), el("span", String(losses), "result-lost"));
  line.append(score, el("span", "Last: " + RESULT_WORDS[last.result].toLowerCase() + " " + last.crowns[0] +
    "–" + last.crowns[1] + ", " + ago(last.time), "muted"));
  const games = el("div");
  for (const m of found.meetings) {
    const row = el("p", null, "meeting");
    row.append(el("span", RESULT_WORDS[m.result] + " " + m.crowns[0] + "–" + m.crowns[1], "result-" + m.result),
      el("span", ago(m.time) + (MODE_NAMES[m.mode] ? ", " + MODE_NAMES[m.mode] : "") +
        (m.archetype ? ", they played " + m.archetype : ""), "muted"));
    games.append(row);
  }
  slot.append(el("p", "Played before", "label"), line, disclosure(plural(found.meetings.length, "game", "games"), games));
}

// Open a player on the board: their decks by game mode, newest first.
async function openOpponent(player, how = {}) {
  const token = ++board.token;
  const lands = board.key !== player.tag;
  board.key = player.tag;
  const head = vsBar(player, how.tag, lands);
  setBoard(head, loadingText("Loading their decks…"));
  boardSay("Your opponent: " + (player.name || player.tag) + (player.clan ? ", " + player.clan : "") + ".");
  const history = el("div");
  fillHistory(player, history);
  let result;
  try {
    result = await api("/api/players/" + tagPath(player.tag) + "/decks");
  } catch (error) {
    if (token === board.token) setBoard(head, errorBox(error, () => openOpponent(player, how)));
    return;
  }
  if (token !== board.token) return;
  const modes = result.modes || {};
  const played = MODES.filter((m) => (modes[m] || []).length)
    .sort((a, b) => newestPlay(modes[b]).localeCompare(newestPlay(modes[a])));
  if (!played.length) {
    setBoard(head, calm(emptyMessage("ranked", result.recent_modes)));
    return;
  }
  const body = el("div");
  let chosen = null;
  const tabs = el("div", null, "subtabs board-modes");
  tabs.setAttribute("role", "tablist");
  tabs.setAttribute("aria-label", "Game mode");
  body.setAttribute("role", "tabpanel");
  body.id = "board-mode-panel";
  const buttons = {};
  async function select(mode) {
    chosen = mode;
    for (const m of played) selectTab(buttons[m], m === mode);
    body.setAttribute("aria-labelledby", buttons[mode].id);
    show(body, loadingText("Working out the odds…"));
    const decks = modes[mode];
    const mine = await myDeckFor(mode);
    const odds = await predict(mine, decks[0].deck);
    if (token !== board.token || chosen !== mode) return;
    const d = decks[0];
    const sub = plural(d.games, "game", "games") + ", " + d.wins + " won" +
      (d.last_played ? ", last " + new Date(d.last_played).toLocaleDateString() : "");
    const parts = boardBody(d.deck, sub, mode, mine, odds, history);
    if (decks.length > 1) {
      const others = el("div");
      for (const other of decks.slice(1)) {
        const when = other.last_played ? ", last " + new Date(other.last_played).toLocaleDateString() : "";
        const label = el("p", "Also played", "label");
        label.append(el("span", plural(other.games, "game", "games") + ", " + other.wins + " won" + when, "sub"));
        const o = await predict(mine, other.deck);
        others.append(label, deckView(other.deck, "theirs"));
        if (o && o.m) others.append(oddsBug(o.m));
      }
      const more = el("div", null, "board-more");
      more.append(disclosure("Other decks they played (" + (decks.length - 1) + ")", others));
      parts.push(more);
    }
    if (token === board.token && chosen === mode) show(body, ...parts);
  }
  for (const mode of played) {
    const games = modes[mode].reduce((n, d) => n + d.games, 0);
    const b = el("button", MODE_NAMES[mode], "tab");
    b.append(el("span", String(games), "n"));
    b.type = "button";
    b.id = "board-mode-" + mode;
    b.setAttribute("role", "tab");
    b.setAttribute("aria-controls", body.id);
    b.addEventListener("click", () => select(mode));
    buttons[mode] = b;
    tabs.append(b);
  }
  setBoard(head, body);
  setTools(played.length > 1 ? tabs : null, wrongPlayer());
  await select(played[0]);
}

// Your last battle's opponent, straight from your battle log.
function showLastOpponent(opp, tag) {
  board.token++;
  const lands = board.key !== opp.tag;
  board.key = opp.tag;
  const head = vsBar(opp, tag, lands);
  const mine = opp.your_deck ? { deck: opp.your_deck, label: "Your deck that battle" } : null;
  const result = opp.matchup ? { m: opp.matchup } : null;
  const recent = action("Their recent decks", () => openOpponent(opp, { tag: { text: "Last opponent" } }));
  const history = el("div");
  fillHistory(opp, history);
  setBoard(head, ...boardBody(opp.deck, MODE_NAMES[opp.mode] || "Battle", opp.mode,
    state.me ? mine : null, result, history, [recent]));
  setTools(wrongPlayer());
  boardSay("Last opponent: " + (opp.name || opp.tag) + ". You " + opp.your_result + ".");
}

const RESULT_TAGS = { won: "you won", lost: "you lost", draw: "a draw" };

async function scoutLast() {
  if (!state.me) {
    boardIdle();
    return;
  }
  const token = ++board.token;
  board.key = null;
  setBoard(vsBar(null, null, false), loadingText("Checking your battle log…"));
  try {
    const opp = await api("/api/players/" + tagPath(state.me.tag) + "/last-opponent");
    if (token !== board.token) return;
    showLastOpponent(opp, { text: "Last battle · " + (RESULT_TAGS[opp.your_result] || opp.your_result) });
  } catch (error) {
    if (token === board.token) setBoard(vsBar(null, null, false), errorBox(error, scoutLast));
  }
}

// --- find an opponent --------------------------------------------------------

function resultList(players) {
  const list = el("ul", null, "results");
  for (const r of players) {
    const item = el("li");
    const button = el("button", null, "result-button");
    button.type = "button";
    // Sure it's them: online right now, or in the clan read off the screen with
    // a name like the one read (name_match is null when no name was given).
    button.dataset.confident = String(Boolean(r.active || (r.clan_match && r.name_match !== false)));
    button.dataset.nameMatch = String(r.name_match !== false);
    button.dataset.active = String(Boolean(r.active));
    button.dataset.clanMatch = String(Boolean(r.clan_match));
    button.append(playerLine(r, { showNoClan: "clan" in r }));
    button.addEventListener("click", () => pickPlayer(r, button));
    item.append(button);
    list.append(item);
  }
  return list;
}

function pickPlayer(player, button) {
  for (const b of document.querySelectorAll("#find-result .result-button")) {
    b.classList.remove("picked");
    const old = b.querySelector(".pick-tag");
    if (old) old.remove();
  }
  button.classList.add("picked");
  const badge = button.dataset.badge;
  if (badge) button.prepend(el("span", badge, "pick-tag"));
  openOpponent(player, { tag: badge ? { text: badge, kind: badge === "Auto-picked" ? "sure" : "doubt" } : null });
  // Picked by hand further down the page: bring the board into view (once).
  if (!button.dataset.auto) document.getElementById("board").scrollIntoView({ block: "start" });
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
      return [calm("The game has no clan called “" + clan + "”.", " Check the spelling.")];
    }
    if (!found.players.length) {
      return [calm("Nobody" + (name ? " with a name like that" : "") + " in " +
        plural(found.clans_checked.length, "clan", "clans") + " called “" + clan + "”.")];
    }
    const note = found.players.some((p) => p.active)
      ? "Whoever is online now is probably them."
      : "Nobody here was online in the last 15 minutes: it may be another clan with this name.";
    return [el("p", "Checked " + plural(found.clans_checked.length, "clan", "clans") + " in the game. " + note,
      "detail"), resultList(found.players)];
  } catch (error) {
    return [errorBox(error, () => liveClanLookup(clan, name, document.getElementById("find-result")))];
  }
}

async function liveClanLookup(clan, name, out) {
  show(out, loadingText("Checking clans named “" + clan + "” in the game…"));
  show(out, ...(await liveClanParts(clan, name)));
}

const NOT_KNOWN_NO_CLAN = " Without a clan the game can't be searched; after the battle, " +
  "Scout last opponent shows their deck.";

// Auto-scout picks the player for you: the first confident match (in the clan
// that was read, or online right now), else the top result with a similar name
// as a best guess, else nobody. Results are already sorted best-first. You can
// still pick someone else. Returns whether anyone was picked.
function autoOpen(out, allowGuess = true) {
  const buttons = [...out.querySelectorAll(".result-button")];
  let pick;
  if (allowGuess) {
    // A best guess must at least have a name like the one read: a clanmate
    // with a different name is never them.
    pick = buttons.find((b) => b.dataset.confident === "true") ||
      buttons.find((b) => b.dataset.nameMatch !== "false");
  } else {
    // Only the clan is known (name unreadable): being in the clan isn't enough,
    // it may have 50 members. Online right now, or the only member we know.
    const inClan = buttons.filter((b) => b.dataset.clanMatch === "true");
    pick = buttons.find((b) => b.dataset.active === "true") || (inClan.length === 1 ? inClan[0] : null);
  }
  if (!pick) return false;
  pick.dataset.badge = pick.dataset.confident === "true" ? "Auto-picked" : "Best guess";
  pick.dataset.auto = "1";
  pick.click();
  delete pick.dataset.auto;
  return true;
}

// Searches, lists the players found under the form, and (auto) opens the best
// one on the board. Returns { picked, count }.
async function findOpponent(event, options = {}) {
  if (event) event.preventDefault();
  const out = document.getElementById("find-result");
  const name = document.getElementById("find-name").value.trim();
  const clan = document.getElementById("find-clan").value.trim();
  const done = (picked) => ({ picked, count: out.querySelectorAll(".result-button").length });
  if (!name && !clan) {
    show(out, errorBox(new Error("Type some of their name, their clan, or both.")));
    return done(false);
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
  show(out, loadingText("Searching…"));
  let results = [];
  try {
    results = (await api(path)).results;
  } catch (error) {
    if (clan.length < 3) {
      show(out, errorBox(error, () => findOpponent(null, options)));
      return done(false);
    }
  }
  const canGoLive = clan.length >= 3;
  const pick = () => (options.auto ? autoOpen(out, !options.nameUnread) : false);
  if (!results.length) {
    if (canGoLive) {
      await liveClanLookup(clan, name, out);  // not in our index: ask the game
      return done(pick());
    }
    show(out, calm("No players found.", noClan ? NOT_KNOWN_NO_CLAN
      : " If their name is emoji or a language you can't type, search by their clan."));
    return done(false);
  }
  if (noClan) {
    if (results.some((r) => r.clan_match)) {
      show(out, el("p", "No clan, so players without one come first. Pick your opponent:", "label"),
        resultList(results));
      return done(pick());
    }
    // Everyone here has a clan, so none of them is likely to be the
    // opponent: list them, but don't open one as if it were.
    show(out, calm("Nobody DeckLens knows without a clan has a name like that.", NOT_KNOWN_NO_CLAN),
      el("p", "Closest names (all in clans, so probably not them)", "label"), resultList(results));
    return done(false);
  }
  const inClan = (r) => r.clan_match && r.name_match !== false;
  if (canGoLive && !results.some(inClan)) {
    // Nobody we know is in that clan (with that name): check the game first,
    // keep what we found underneath in case the clan or name was misread.
    show(out, loadingText("Nobody DeckLens knows is in that clan. Checking the game…"));
    const live = await liveClanParts(clan, name);
    show(out, ...live, el("p", "Others DeckLens knows with a similar name or clan", "label"), resultList(results));
    return done(pick());
  }
  const parts = [el("p", "Pick your opponent", "label"), resultList(results)];
  if (canGoLive) parts.push(action("Not here? Check the clan live", () => liveClanLookup(clan, name, out)));
  show(out, ...parts);
  return done(pick());
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
function battleUnreadable() {
  auto.afterBattle = state.me ? { before: lastOpponentNow().then((o) => o.time, () => null) } : null;
  boardUnreadable();
}

async function showAfterBattle() {
  const token = ++auto.afterToken;
  const before = await auto.afterBattle.before;
  auto.afterBattle = null;
  setBoard(vsBar({ name: "Name unreadable" }, { text: "Battle over", kind: "doubt" }, false),
    loadingText("Getting your opponent from your battle log…"));
  for (let attempt = 0; attempt < AFTER_BATTLE_TRIES; attempt++) {
    if (attempt) await new Promise((r) => setTimeout(r, AFTER_BATTLE_WAIT_MS));
    if (token !== auto.afterToken) return;  // a newer battle took over
    let opp = null;
    try {
      opp = await lastOpponentNow();
    } catch { /* not in the log yet, or offline for a moment */ }
    if (opp && opp.time !== before) {
      showLastOpponent(opp, { text: "From your battle log" });
      return;
    }
  }
  if (token === auto.afterToken) {
    setBoard(vsBar(null, null, false), calm("Your battle log hasn't updated yet.", " Try again in a minute.",
      action("Scout last opponent", scoutLast, "primary them")));
  }
}

// Whether the screen watcher can see your phone: [kind, chip, short line, full sentence].
const WATCHER_STATES = {
  connected: ["on", "Live", (d) => d || "Your phone", (d) => "Connected to " + (d || "your phone screen")],
  screen_only: ["warn", "Keep visible", (d) => d || "Your phone",
    (d) => "Connected to " + (d || "your phone screen") + ", but keep that window visible: it can't be read behind other windows"],
  starting: ["idle", "Starting", () => "Screen reader", () => "Starting the screen reader"],
  no_window: ["off", "Not connected", () => "Open Phone Link",
    () => "Not connected: open your phone screen in Phone Link (or mirror your iPad to 5KPlayer)"],
  minimised: ["off", "Not connected", (d) => (d || "Mirror") + " minimised",
    (d) => "Not connected: " + (d || "the mirror window") + " is minimised (it can sit behind other windows)"],
  unavailable: ["off", "Not connected", () => "Watcher unavailable",
    (d) => "Not connected: " + (d || "the screen watcher can't run")],
};
const WATCHER_OFF = ["off", "Not connected", () => "Watcher not running",
  () => "Not connected: the screen watcher isn't running"];

function showWatcherStatus(watcher, latest) {
  const status = document.getElementById("auto-status");
  const [kind, chip, short, long] = (watcher && WATCHER_STATES[watcher.state]) || WATCHER_OFF;
  const detail = watcher ? watcher.detail : null;
  let line = short(detail);
  let sentence = long(detail);
  if (kind === "on" || kind === "warn") {
    let now = "waiting for a battle";
    if (latest && latest.kind === "unreadable") now = "name unreadable";
    else if (latest && latest.kind !== "ended") {
      now = "read " + (latest.name || "name unreadable") + (latest.clan ? " (" + latest.clan + ")" : "");
    }
    line += " · " + now;
    sentence += ". " + now[0].toUpperCase() + now.slice(1) + ".";
  }
  const what = el("span", line, "what");
  status.title = sentence;
  status.setAttribute("aria-label", sentence);
  show(status, el("span", chip, "state " + kind), what);
  document.getElementById("rescan-button").hidden = !(kind === "on" || kind === "warn");
}

// Ask the screen watcher to read this battle's opponent again (a misread name,
// or the wrong player picked). The new read arrives through pollWatcher.
async function rescanOpponent() {
  const button = document.getElementById("rescan-button");
  button.disabled = true;
  button.textContent = "Reading…";
  try {
    await api("/api/watch/rescan", { method: "POST" });
  } catch (error) {
    button.textContent = error.message;
  }
  setTimeout(() => {
    button.disabled = false;
    button.textContent = "Read again";
  }, 4000);
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
  if (latest.kind === "ended") {
    if (auto.afterBattle) await showAfterBattle();
    return;
  }
  // A battle started: show the board, at the top.
  setPage("scout");
  window.scrollTo(0, 0);
  if (latest.kind === "unreadable") {
    battleUnreadable();
    return;
  }
  auto.afterBattle = null;  // this battle's opponent was read
  document.getElementById("find-name").value = latest.name || "";
  document.getElementById("find-clan").value = latest.clan || "";
  boardFinding(latest);
  const token = board.token;
  // No name (fancy letters or symbols): search the clan, but only open
  // someone we're sure of: a guess from the clan alone is usually wrong.
  const found = await findOpponent(null, { auto: true, nameUnread: !latest.name,
    noClan: Boolean(latest.name && !latest.clan), rating: latest.rating });
  if (!found.picked && token === board.token) boardNotFound(latest, found.count);
}

function setAutoScout(on) {
  storageSet("decklens.autoScout", on ? "1" : "0");
  const status = document.getElementById("auto-status");
  document.getElementById("watch-bug").hidden = !on;
  document.getElementById("rescan-button").hidden = true;  // shown once the watcher is connected
  clearInterval(auto.timer);
  auto.timer = null;
  auto.lastId = null;
  if (on) {
    show(status, el("span", "Starting", "state idle"), el("span", "Checking the screen watcher", "what"));
    pollWatcher();
    auto.timer = setInterval(pollWatcher, 2000);
  }
  if (board.key === null) boardIdle();
}

const MODE_NAMES = { ranked: "Ranked", trophy: "Trophy Road", challenge: "20-win challenge" };
const MODES = ["ranked", "trophy", "challenge"];

function emptyMessage(mode, recent) {
  const entries = Object.entries(recent || {});
  const total = entries.reduce((sum, [, n]) => sum + n, 0);
  if (!total) return "This player has no battles in their recent battle log.";
  const list = entries.map(([name, n]) => name + " " + n).join(", ");
  const name = MODE_NAMES[mode];
  if (recent[name]) return "Their recent " + name + " battles couldn't be read (" + list + ").";
  return "No Ranked, Trophy Road or challenge games in their last " + total + " battles. Recently: " + list + ".";
}

// Which of your decks to compare against: your current deck for Ranked; for
// Trophy Road and the 20-win challenge, your latest deck in that mode (you may
// play a different deck there), falling back to your current deck.
const MY_MODE_LABELS = { trophy: "Trophy Road", challenge: "challenge" };

async function myDeckFor(mode) {
  if (!state.me) return null;
  if (mode === "ranked") return { deck: state.me.deck, label: "Your current deck" };
  if (state.myDecks === undefined) {  // your own log, fetched once
    state.myDecks = api("/api/players/" + tagPath(state.me.tag) + "/decks")
      .then((mine) => mine.modes || {}).catch(() => ({}));
  }
  const decks = ((await state.myDecks)[mode] || []).slice();
  decks.sort((a, b) => String(b.last_played).localeCompare(String(a.last_played)));
  const name = MY_MODE_LABELS[mode] || "matching";
  if (decks.length) return { deck: decks[0].deck, label: "Your latest " + name + " deck" };
  return { deck: state.me.deck, label: "Your current deck (no recent " + name + " deck found)" };
}

function ago(iso) {
  const minutes = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (minutes < 60) return plural(minutes, "minute", "minutes") + " ago";
  if (minutes < 48 * 60) return plural(Math.round(minutes / 60), "hour", "hours") + " ago";
  return plural(Math.round(minutes / 1440), "day", "days") + " ago";
}

const RESULT_WORDS = { won: "Won", lost: "Lost", draw: "Draw" };

// --- ranked meta ---------------------------------------------------------------

// 14 tiny columns: how much of the field this archetype was, day by day.
function trendBars(daily) {
  const W = 6, GAP = 2, H = 28;
  const top = Math.max(...daily.map((d) => d.share || 0), 0.0001);
  const chart = svg("svg", { viewBox: "0 0 " + daily.length * (W + GAP) + " " + H, class: "trend" });
  chart.setAttribute("role", "img");
  const first = daily.find((d) => d.share !== null), last = [...daily].reverse().find((d) => d.share !== null);
  chart.setAttribute("aria-label", "Play rate by day" + (first && last
    ? ", from " + pct(first.share) + " to " + pct(last.share) : ""));
  daily.forEach((d, i) => {
    const h = d.share ? Math.max(1, (d.share / top) * H) : 0;
    if (h) chart.append(svg("rect", { x: i * (W + GAP), y: H - h, width: W, height: h }));
  });
  return chart;
}

function svg(tag, attrs) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, String(v));
  return node;
}

function metaTable(m) {
  const wrap = el("div", null, "table-wrap");
  const table = el("table", null, "tiers meta");
  table.append(el("thead"), el("tbody"));
  const head = el("tr");
  for (const h of ["Archetype", "Played", "Wins", "Last 14 days"]) head.append(el("th", h));
  table.tHead.append(head);
  for (const a of m.archetypes) {
    const tr = el("tr");
    const name = el("th");
    const open = el("button", a.name, "meta-name");
    open.type = "button";
    open.setAttribute("aria-expanded", "false");
    name.append(open);
    tr.append(name);
    tr.append(el("td", pct(a.share), "rate"));
    const wins = el("td", null, "record");
    if (a.games) {
      const lean = a.range[0] > 0.5 ? " good" : a.range[1] < 0.5 ? " bad" : "";
      wins.append(el("span", pct(a.win_rate), "rate" + lean),
        el("span", plural(a.games, "game", "games"), "games"),
        el("span", pct(a.range[0]) + "–" + pct(a.range[1]), "range"));
    } else {
      wins.append(el("span", "—", "rate"));
    }
    tr.append(wins);
    const trend = el("td");
    trend.append(trendBars(a.daily));
    tr.append(trend);
    // Open the archetype: its most played decks, card by card.
    const detail = el("tr", null, "meta-decks");
    detail.hidden = true;
    const cell = el("td");
    cell.colSpan = 4;
    for (const d of a.decks || []) {
      const line = el("p", null, "meeting");
      line.append(el("span", plural(d.games, "game", "games")), el("span", pct(d.win_rate) + " won", "muted"));
      cell.append(line, miniDeck(d), copyDeckButton(knownIds(d)));
    }
    detail.append(cell);
    open.addEventListener("click", () => {
      detail.hidden = !detail.hidden;
      open.setAttribute("aria-expanded", String(!detail.hidden));
    });
    table.tBodies[0].append(tr, detail);
  }
  wrap.append(table);
  return wrap;
}

async function showMeta(top = 1000) {
  const out = document.getElementById("meta-result");
  show(out, loadingText("Loading the meta…"));
  let m;
  try {
    m = await api("/api/meta?top=" + encodeURIComponent(top));
  } catch (error) {
    show(out, errorBox(error, () => showMeta(top), action("Go to Scout", () => setPage("scout"))));
    return;
  }
  const tiers = document.getElementById("meta-bands");
  tiers.replaceChildren(...m.tiers.map((t) => {
    const button = action(t.label, () => showMeta(t.top), "filter");
    button.setAttribute("aria-pressed", String(t.top === m.top));
    return button;
  }));
  if (!m.archetypes.length) {
    show(out, calm("No stored battles between two top " + m.top.toLocaleString() + " players yet."));
    return;
  }
  show(out, el("p", m.decks.toLocaleString() + " decks · both players in the global top " +
    m.top.toLocaleString() + " · open an archetype for its decks", "detail"), metaTable(m));
}

// --- pages ---------------------------------------------------------------------

// You, Scout and Meta are separate pages, so each stays short. Auto-scout jumps
// to Scout when a battle starts. The meta loads the first time it's opened.
let metaLoaded = false;

function setPage(key) {
  for (const tab of document.querySelectorAll(".pages [data-page]")) {
    const on = tab.dataset.page === key;
    selectTab(tab, on);
    document.getElementById("page-" + tab.dataset.page).hidden = !on;
  }
  storageSet("decklens.page", key);
  if (key === "meta" && !metaLoaded) {
    metaLoaded = true;
    showMeta();
  }
}

// --- start -------------------------------------------------------------------

async function showAccount() {
  try {
    const me = await api("/api/me");
    state.local = Boolean(me.desktop || !me.login);
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
  document.getElementById("account").hidden = false;
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
  document.getElementById("find-form").addEventListener("submit", findOpponent);
  const autoBox = document.getElementById("auto-scout");
  autoBox.addEventListener("change", () => setAutoScout(autoBox.checked));
  document.getElementById("rescan-button").addEventListener("click", rescanOpponent);
  autoBox.checked = storageGet("decklens.autoScout") === "1";
  boardIdle();
  if (autoBox.checked) setAutoScout(true);
  for (const tab of document.querySelectorAll(".pages [data-page]")) {
    tab.addEventListener("click", () => setPage(tab.dataset.page));
  }
  const page = storageGet("decklens.page");
  setPage(["you", "scout", "meta"].includes(page) ? page : "you");
  const saved = asTag(storageGet("decklens.myTag") || "");
  if (saved) {
    document.getElementById("me-tag").value = saved;
    showMe(saved);
  }
});
