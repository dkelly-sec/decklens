// DeckLens front end.
// Safety rule: data from the server is only ever put on the page with
// textContent (via el()), never innerHTML, so a player named "<script>..."
// shows up as plain text. The Content Security Policy blocks inline script too.
"use strict";

const state = { me: null };

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

// "Loading…" text with a turning ring, announced politely to screen readers.
function loadingText(text) {
  const p = el("p", text, "muted loading");
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

// side: "yours" or "theirs" (colours the deck blue or red). Their decks also
// get a read of the spells and buildings to play around.
function deckView(deck, side) {
  const box = el("div", null, "deck-box" + (side ? " " + side : ""));
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
  if (knownIds(deck).length >= 8) box.append(copyDeckButton(knownIds(deck)));
  if (side === "theirs") {
    const read = scoutRead(deck.cards);
    if (read) box.append(read);
  }
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

const READ_KINDS = [["spell", "Spells"], ["building", "Buildings"]];

function scoutRead(cards) {
  const rows = [];
  for (const [kind, title] of READ_KINDS) {
    const these = cards.filter((c) => c.kind === kind);
    if (!these.length) continue;
    const row = el("p");
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
  const box = el("div", null, "scout-read");
  box.append(...rows);
  return box;
}

// Card ids, then a "form token" for each active Evolution or Hero, as the server's
// models expect (battles.FORM_TOKEN: Evo Giant is not plain Giant).
const FORM_TOKEN = { evo: 1000000000, hero: 2000000000 };

// "Copy to game": a QR code to scan with your phone (the app is on your PC), the
// link itself, and a copy button. ids: card ids in slot order, then form tokens.
function copyDeckButton(ids) {
  const wrap = el("div", null, "copy-deck");
  const button = el("button", "Copy to game", "link-button");
  button.type = "button";
  button.setAttribute("aria-expanded", "false");
  const panel = el("div", null, "copy-panel");
  panel.hidden = true;
  button.addEventListener("click", async () => {
    const opening = panel.hidden;
    panel.hidden = !opening;
    button.setAttribute("aria-expanded", String(opening));
    if (!opening || panel.childElementCount) return;
    show(panel, loadingText("Making the link…"));
    try {
      const r = await api("/api/deck-link", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ deck: ids }),
      });
      const copy = el("button", "Copy link", "link-button");
      copy.type = "button";
      copy.addEventListener("click", async () => {
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
      show(panel, qrCode(r.qr), el("p", "Scan with your phone's camera to open this deck in " +
        "Clash Royale.", "hint"), actions);
    } catch (error) {
      show(panel, errorBox(error));
    }
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

// Names and clans go in <bdi> so right-to-left text (Arabic, Hebrew) stays
// inside its own part and can't reorder the tag and numbers around it.
function playerLine(player, options = {}) {
  const line = el("p", null, "player");
  line.append(el("bdi", player.name || "Unknown", "name"));
  const facts = el("span", null, "facts");
  const add = (node, className) => facts.append(typeof node === "string" ? el("span", node, className) : node);
  add(player.tag);
  if (player.clan) add(el("bdi", player.clan));
  else if (options.showNoClan) add("no clan");
  if (player.rank) add("rank #" + player.rank);
  if (player.rating) add("rating " + player.rating);
  if (player.trophies) add(player.trophies.toLocaleString() + " trophies");
  if (player.last_seen_minutes !== null && player.last_seen_minutes !== undefined) {
    add(seenText(player.last_seen_minutes), player.last_seen_minutes < 2 ? "online" : undefined);
  }
  line.append(facts);  // every part is a text node: still never HTML
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
    const lean = cell.range[0] > 0.5 ? " good" : cell.range[1] < 0.5 ? " bad" : "";
    td.append(el("span", pct(cell.rate), "rate" + lean));
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

// Your win chance as a tug of war: blue pushes from the left, red from the right.
function tugBar(m) {
  const you = m.mirror ? 0.5 : m.win_chance;
  const tug = el("div", null, "tug" + (m.mirror ? " mirror" : ""));
  const numbers = el("div", null, "tug-numbers");
  numbers.setAttribute("aria-hidden", "true");  // the sentence below says the same
  const a = el("span", pct(you), "you");
  a.append(el("small", "you"));
  const b = el("span", pct(1 - you), "them");
  b.append(el("small", "them"));
  numbers.append(a, b);
  const bar = el("div", null, "tug-bar");
  bar.setAttribute("aria-hidden", "true");
  tug.append(numbers, bar);
  // Start level and slide to the result, so the eye catches which way it leans.
  requestAnimationFrame(() => requestAnimationFrame(() => bar.style.setProperty("--p", (you * 100).toFixed(1) + "%")));
  return tug;
}

// decks: { mine, theirs } when both are known, for the share image.
function matchupBox(m, label, mode, decks) {
  const box = el("div", null, "matchup");
  box.append(el("p", label || "Matchup", "label"));
  const names = el("p", null, "archetypes");
  names.append(el("span", m.archetype_a, "a"), el("span", m.archetype_b, "b"));
  box.append(names, tugBar(m));
  let why = "";
  if (m.based_on) {
    why = " From " + plural(m.based_on.games, "game", "games") + " between " + m.based_on.words +
      " (same win conditions, spells, Evos and Heroes), pulled toward " +
      (m.estimate === "model" ? "the model's" : "the archetype") + " estimate of " +
      pct(m.estimate_chance) + " by how few games there are.";
  }
  box.append(el("p", m.mirror ? "Mirror match: a coin flip on decks."
    : "Your deck wins about " + pct(m.win_chance) + " of games like this." + why, "chance"));
  if (m.breakdown) {
    box.append(breakdownTable(m.breakdown));
    box.append(el("p", "Your win rate, from ranked games in the last " + m.breakdown.window_days +
      " days. Exact: both decks card for card. 7+ / 6+ of 8: each deck shares at least that " +
      "many cards with yours and theirs. Archetype: same deck styles. Below Exact, both decks " +
      "also keep the same win condition and spells (Rocket instead of Fireball is a different " +
      "matchup). Top 200 / 1,000 only " +
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
      const item = el("li");
      item.append(el("span", k.card, k.effect > 0 ? "yours" : "theirs"), " (" + where + ") " + effect);
      cards.append(item);
    }
    box.append(el("p", "Cards that matter most:", "detail"), cards);
  }
  if (decks && decks.mine && decks.theirs) box.append(shareButton(m, decks.mine, decks.theirs));
  return box;
}

// --- share image ----------------------------------------------------------------

const SHARE = { w: 1080, h: 1440, bg: "#131a2b", panel: "#1b2439", text: "#edf0f7",
  muted: "#a3adc8", blue: "#5b95ff", red: "#ff6168", elixir: "#d968ff" };

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

// One deck: 4 x 2 cards on a tinted panel, with its archetype above.
async function drawDeck(ctx, deck, x, y, colour, title) {
  const C = SHARE, cw = 180, ch = 216, gap = 16;
  ctx.fillStyle = colour;
  ctx.font = "700 44px 'Barlow Condensed', sans-serif";
  ctx.fillText(title, x, y + 40);
  ctx.fillStyle = C.panel;
  ctx.beginPath();
  ctx.roundRect(x - 16, y + 60, 4 * cw + 3 * gap + 32, 2 * ch + gap + 32, 24);
  ctx.fill();
  ctx.fillStyle = colour;
  ctx.fillRect(x - 16, y + 60, 8, 2 * ch + gap + 32);
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
  // The verdict between the decks: the tug of war.
  const you = m.mirror ? 0.5 : m.win_chance, top = 590;
  ctx.font = "700 120px 'Barlow Condensed', sans-serif";
  ctx.fillStyle = C.blue;
  ctx.fillText(pct(you), 60, top + 100);
  ctx.fillStyle = C.red;
  ctx.textAlign = "right";
  ctx.fillText(pct(1 - you), C.w - 60, top + 100);
  ctx.textAlign = "left";
  ctx.fillStyle = C.red;
  ctx.beginPath();
  ctx.roundRect(60, top + 124, C.w - 120, 28, 14);
  ctx.fill();
  ctx.save();
  ctx.clip();
  ctx.fillStyle = C.blue;
  ctx.fillRect(60, top + 124, (C.w - 120) * you, 28);
  ctx.restore();
  ctx.fillStyle = C.bg;
  ctx.fillRect(C.w / 2 - 2, top + 118, 4, 40);
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
  const button = el("button", "Share image", "link-button");
  button.type = "button";
  const panel = el("div", null, "share-panel");
  panel.hidden = true;
  button.addEventListener("click", async () => {
    panel.hidden = !panel.hidden;
    if (panel.hidden || panel.childElementCount) return;
    show(panel, loadingText("Drawing…"));
    const canvas = await shareCanvas(m, mine, theirs);
    canvas.className = "share-preview";
    canvas.setAttribute("role", "img");
    canvas.setAttribute("aria-label", "Share image: " + m.archetype_a + " vs " + m.archetype_b +
      ", " + pct(m.win_chance) + " to " + pct(1 - m.win_chance));
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
    const save = el("a", "Download PNG", "link-button");
    save.href = URL.createObjectURL(blob);
    save.download = "decklens-" + (m.archetype_a + "-vs-" + m.archetype_b).toLowerCase()
      .replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") + ".png";
    const copy = el("button", "Copy image", "link-button");
    copy.type = "button";
    copy.addEventListener("click", async () => {
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
    show(out, playerLine(me), ...youTabs(me));
  } catch (error) {
    state.me = null;
    state.myDecks = undefined;
    show(out, errorBox(error));
  }
}

// Deck, record and tweaks share one spot, so the page stays short. A tab is
// built the first time it's opened; the last one you used is remembered.
const YOU_TABS = [["deck", "Deck"], ["today", "Today"], ["record", "Record"], ["rank", "Rank"],
  ["tweaks", "Tweaks"]];

function youTabs(me) {
  const bar = el("div", null, "tabs strip");
  bar.setAttribute("role", "tablist");
  bar.setAttribute("aria-label", "About you");
  const panel = el("div", null, "tab-panel");
  panel.setAttribute("role", "tabpanel");
  panel.id = "you-panel";
  const built = {};
  const buttons = {};

  function build(key) {
    const box = el("div");
    if (key === "deck") {
      box.append(me.deck.cards.length ? deckView(me.deck, "yours")
        : el("p", "No current deck in your profile.", "muted"));
    } else if (key === "record") {
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
    if (buttons[key].scrollIntoView) buttons[key].scrollIntoView({ block: "nearest", inline: "nearest" });
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
  select(YOU_TABS.some(([k]) => k === saved) ? saved : "deck");
  return [bar, panel];
}

// --- today's session ------------------------------------------------------------

function refreshButton(onClick) {
  const b = el("button", "Refresh", "link-button");
  b.type = "button";
  b.addEventListener("click", onClick);
  return b;
}

async function showSession(tag, box) {
  show(box, loadingText("Reading your battle log…"));
  let s;
  try {
    s = await api("/api/players/" + tagPath(tag) + "/session");
  } catch (error) {
    show(box, errorBox(error), refreshButton(() => showSession(tag, box)));
    return;
  }
  if (!s.games) {
    show(box, el("p", "No Ranked games in your recent battle log.", "muted"),
      refreshButton(() => showSession(tag, box)));
    return;
  }
  const head = el("div", null, "session-head");
  const score = el("p", null, "session-score");
  score.append(el("span", String(s.wins), "result-won"), el("span", "–"),
    el("span", String(s.losses), "result-lost"));
  head.append(score, el("p", pct(s.wins / Math.max(1, s.wins + s.losses)) + " won, " +
    plural(s.games, "game", "games") + " since " + new Date(s.started).toLocaleTimeString([],
      { hour: "numeric", minute: "2-digit" }), "detail"));
  const parts = [head];
  // Oldest on the left, like reading the session from the start.
  const strip = el("ol", null, "session-strip");
  strip.setAttribute("aria-label", "Results, oldest first");
  for (const r of [...s.results].reverse()) {
    const dot = el("li", r === "won" ? "W" : r === "lost" ? "L" : "D", "dot-" + r);
    strip.append(dot);
  }
  parts.push(strip);
  const st = s.streak;
  parts.push(el("p", (st.result === "won" ? "Won " : st.result === "lost" ? "Lost " : "Drew ") +
    st.games + " in a row.", "detail"));
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

// Rank over time, best (lowest number) at the top.
function rankChart(points) {
  const W = 600, H = 180, PAD = 8;
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
  return chart;
}

async function showRank(tag, box, days) {
  const tabs = el("div", null, "chips");
  tabs.setAttribute("role", "group");
  tabs.setAttribute("aria-label", "How far back");
  for (const d of RANK_DAYS) {
    const b = el("button", d + " days", "chip-button");
    b.type = "button";
    b.setAttribute("aria-pressed", String(d === days));
    b.addEventListener("click", () => showRank(tag, box, d));
    tabs.append(b);
  }
  show(box, tabs, loadingText("Loading your rank…"));
  let h;
  try {
    h = await api("/api/players/" + tagPath(tag) + "/rank-history?days=" + days);
  } catch (error) {
    show(box, tabs, errorBox(error));
    return;
  }
  const pts = h.points;
  if (!pts.length) {
    show(box, tabs, el("p", "No rank yet this season (you need a global rank, about the top " +
      "10,000).", "muted"));
    return;
  }
  const now = pts[pts.length - 1].rank;
  const best = Math.min(...pts.map((p) => p.rank));
  const head = el("p", null, "session-score");
  head.append(el("span", "#" + now.toLocaleString()));
  const parts = [tabs, head, el("p", "Best #" + best.toLocaleString() + " in the last " + days +
    " days. Higher on the chart is better.", "detail")];
  if (pts.length > 1) {
    const chart = el("div", null, "rank-wrap");
    chart.append(rankChart(pts));
    const axis = el("p", null, "rank-axis");
    axis.append(el("span", new Date(pts[0].time).toLocaleDateString()), el("span", "now"));
    parts.push(chart, axis);
  } else {
    parts.push(el("p", "One reading so far: the graph fills in as your games are collected " +
      "(every 15 minutes while the app is open, with DECKLENS_MY_TAGS set).", "note"));
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
  show(box, el("p", "Deck tweaks", "label"),
    loadingText("Looking at stored decks one card away from yours…"));
  let t;
  try {
    t = await api("/api/tweaks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ deck: knownIds(deck), band }),
    });
  } catch (error) {
    show(box, errorBox(error));
    return;
  }
  const bands = t.bands.filter((b) => b.base !== undefined);
  if (!t.swaps.length || !bands.length) {
    show(box, el("p", "No version of your deck with one card different has 10+ stored games " +
      (band === "all" ? "" : "in this range ") + "yet. Try another range, or check back after more crawls.", "muted"));
    return;
  }
  const sorter = el("div", null, "chips");
  sorter.setAttribute("role", "group");
  sorter.setAttribute("aria-label", "Sort by rank range");
  for (const b of bands) {
    const button = el("button", b.label, "chip-button");
    button.type = "button";
    button.setAttribute("aria-pressed", String(b.key === band));
    button.addEventListener("click", () => showTweaks(deck, box, b.key));
    sorter.append(button);
  }
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
  show(box, el("p", "Best swaps for:", "hint"), sorter, wrap,
    el("p", "Real stored Ranked games of decks that share 7 of your 8 cards: the swap is the card " +
      "they play instead of one of yours. Win rates are pulled toward all decks close to yours " +
      "(6+ of 8 cards) by 20 games, so a lucky 3–0 doesn't top the list. Top 10,000 / 1,000 go " +
      "by each player's global rank when they played. Swaps need 10+ games in the column you " +
      "sort by.", "note"));
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
      img.width = 36;
      img.height = 43;
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
// tap a row to see your latest games against it.
async function showRecord(tag, box, days) {
  const tabs = el("div", null, "chips");
  tabs.setAttribute("role", "group");
  tabs.setAttribute("aria-label", "How far back");
  for (const d of RECORD_DAYS) {
    const b = el("button", d + " days", "chip-button");
    b.type = "button";
    b.setAttribute("aria-pressed", String(d === days));
    b.addEventListener("click", () => showRecord(tag, box, d));
    tabs.append(b);
  }
  show(box, tabs, loadingText("Loading your games…"));
  let r;
  try {
    r = await api("/api/players/" + tagPath(tag) + "/record?days=" + days);
  } catch (error) {
    show(box, tabs, errorBox(error));
    return;
  }
  const parts = [tabs];
  if (!r.games) {
    parts.push(el("p", "No Ranked games in this window yet.", "muted"));
  } else {
    parts.push(el("p", "Ranked: " + r.wins + " wins from " + plural(r.games, "game", "games") +
      " (" + pct(r.wins / r.games) + ").", "detail"));
    const search = el("input", null, "record-search");
    search.type = "search";
    search.placeholder = "Find a win condition, deck or card, e.g. golem or rocket";
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
    parts.push(el("p", "Only your last ~25 battles are counted. To keep a longer record, add your " +
      "tag to DECKLENS_MY_TAGS in .env: your games are then saved every 15 minutes while the app " +
      "is open, and with each scheduled crawl.", "note"));
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
    else show(out, el("p", "That isn't a valid player tag.", "error"));
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
    deckView(opp.deck, "theirs"),
  ];
  if (opp.matchup) {
    parts.push(matchupBox(opp.matchup, "Your deck vs theirs", opp.mode,
      { mine: opp.your_deck, theirs: opp.deck }));
  }
  return parts;
}

async function scoutLast() {
  const out = document.getElementById("last-result");
  if (!state.me) {
    show(out, el("p", "Look yourself up first.", "error"));
    return;
  }
  show(out, loadingText("Checking your battle log…"));
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
    // Sure it's them: online right now, or in the clan read off the screen with
    // a name like the one read (name_match is null when no name was given).
    button.dataset.confident = String(Boolean(r.active || (r.clan_match && r.name_match !== false)));
    button.dataset.nameMatch = String(r.name_match !== false);
    button.dataset.active = String(Boolean(r.active));
    button.dataset.clanMatch = String(Boolean(r.clan_match));
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
  show(out, loadingText("Checking clans named \u201c" + clan + "\u201d in the game…"));
  show(out, ...(await liveClanParts(clan, name)));
}

const NOT_KNOWN_NO_CLAN = "They're probably not in DeckLens yet, and without a clan the game " +
  "can't be asked (it only searches by clan). After the battle, \u201cScout my last opponent\u201d shows their deck.";

// Auto-scout picks the player for you: the first confident match (in the clan
// that was read, or online right now), else the top result with a similar name
// as a best guess, else nobody.
// Results are already sorted best-first. You can still tap someone else.
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
  show(out, loadingText("Searching…"));
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
  const inClan = (r) => r.clan_match && r.name_match !== false;
  if (canGoLive && !results.some(inClan)) {
    // Nobody we know is in that clan (with that name): check the game first,
    // keep what we found underneath in case the clan or name was misread.
    show(out, el("p", "Nobody DeckLens knows " + (name ? "with that name " : "") +
      "is in that clan. Checking the game…", "muted loading"));
    const live = await liveClanParts(clan, name);
    show(out, ...live, el("p", "Other players DeckLens knows with a similar name or clan:", "hint"), resultList(results));
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
  show(out, loadingText("Battle over. Getting your opponent from your battle log…"));
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
  if (latest.kind !== "ended") setPage("scout");   // a battle started: show the opponent
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

async function matchupFor(mine, deck, mode) {
  if (!mine || knownIds(mine.deck).length < 7 || knownIds(deck).length < 7) return [];
  try {
    const m = await api("/api/predict", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ deck_a: knownIds(mine.deck), deck_b: knownIds(deck) }),
    });
    return [matchupBox(m, mine.label, mode, { mine: mine.deck, theirs: deck })];
  } catch (error) {
    return [errorBox(error)];
  }
}

async function deckList(decks, mode) {
  const mine = await myDeckFor(mode);
  // All matchups at once rather than one after another: the tab opens sooner.
  const matchups = await Promise.all(decks.map((d) => matchupFor(mine, d.deck, mode)));
  return decks.flatMap((d, i) => {
    const when = d.last_played ? ", last on " + new Date(d.last_played).toLocaleDateString() : "";
    return [el("p", "Played " + plural(d.games, "time", "times") + " (" + d.wins + " wins" + when + ")", "label"),
      deckView(d.deck, "theirs"), ...matchups[i]];
  });
}

function newestPlay(decks) {
  return decks.reduce((best, d) => (String(d.last_played) > best ? String(d.last_played) : best), "");
}

function ago(iso) {
  const minutes = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (minutes < 60) return plural(minutes, "minute", "minutes") + " ago";
  if (minutes < 48 * 60) return plural(Math.round(minutes / 60), "hour", "hours") + " ago";
  return plural(Math.round(minutes / 1440), "day", "days") + " ago";
}

const RESULT_WORDS = { won: "Won", lost: "Lost", draw: "Draw" };

// "You've played them before": earlier games against this player, newest first.
async function rematchBox(player) {
  if (!state.me || !player.tag || player.tag === state.me.tag) return null;
  let found;
  try {
    found = await api("/api/players/" + tagPath(state.me.tag) + "/vs/" + tagPath(player.tag));
  } catch {
    return null;  // optional extra: the decks below still load
  }
  if (!found.meetings.length) return null;
  const box = el("div", null, "rematch");
  box.append(el("p", "You've played them before", "label"));
  for (const m of found.meetings) {
    const line = el("p", null, "meeting");
    line.append(el("span", RESULT_WORDS[m.result] + " " + m.crowns[0] + "–" + m.crowns[1],
      "result-" + m.result));
    line.append(el("span", ago(m.time) + (MODE_NAMES[m.mode] ? ", " + MODE_NAMES[m.mode] : "") +
      (m.archetype ? ", they played " + m.archetype : ""), "muted"));
    box.append(line);
    const read = scoutRead(m.deck.cards);
    if (read) box.append(read);
  }
  return box;
}

async function showOpponentDecks(player, container) {
  const panel = el("div", null, "decks-panel");
  panel.append(loadingText("Loading their recent decks…"));
  const old = container.querySelector(".decks-panel");
  if (old) old.remove();
  container.append(panel);
  const rematch = rematchBox(player);  // in parallel with their decks
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
    for (const m of MODES) selectTab(buttons[m], m === mode);
    body.setAttribute("aria-labelledby", buttons[mode].id);
    if (!rendered[mode]) {  // built once per tab; a promise, so fast clicks don't build twice
      const decks = modes[mode] || [];
      rendered[mode] = decks.length ? deckList(decks, mode)
        : Promise.resolve([el("p", emptyMessage(mode, result.recent_modes), "muted")]);
    }
    show(body, loadingText("Loading matchups…"));
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
  const before = await rematch;
  if (before) panel.prepend(before);
  const first = MODES.slice().sort((a, b) => newestPlay(modes[b] || []).localeCompare(newestPlay(modes[a] || [])))[0];
  await select(first);
}

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
    if (h) chart.append(svg("rect", { x: i * (W + GAP), y: H - h, width: W, height: h, rx: 1 }));
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
    // Tap the archetype: its most played decks, card by card.
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
    show(out, errorBox(error));
    return;
  }
  const tiers = document.getElementById("meta-bands");
  tiers.replaceChildren(...m.tiers.map((t) => {
    const button = el("button", t.label, "chip-button");
    button.type = "button";
    button.setAttribute("aria-pressed", String(t.top === m.top));
    button.addEventListener("click", () => showMeta(t.top));
    return button;
  }));
  if (!m.archetypes.length) {
    show(out, el("p", "No stored battles between two top " + m.top.toLocaleString() +
      " players yet.", "muted"));
    return;
  }
  show(out, el("p", m.decks.toLocaleString() + " decks from the last " + m.days + " days, only " +
    "from battles where both players were in the global top " + m.top.toLocaleString() +
    " when they played. Tap an archetype to see its most played decks.", "hint"), metaTable(m));
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
