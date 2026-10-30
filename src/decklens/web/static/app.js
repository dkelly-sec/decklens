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

function cardTile(card) {
  const tile = el("li", null, "card" + (card.rarity ? " " + card.rarity : ""));
  const img = document.createElement("img");
  img.src = "/cards/" + encodeURIComponent(card.id) + ".png" + (card.evolved ? "?evo=1" : "");
  img.alt = card.name;
  img.width = 64;
  img.height = 77;
  img.loading = "lazy";
  img.addEventListener("error", () => img.remove());  // no image: the name below still shows
  tile.append(img, el("span", card.name, "card-name"));

  const facts = [];
  if (card.elixir !== null && card.elixir !== undefined) facts.push(card.elixir + " elixir");
  if (card.evolved) facts.push("Evo");
  if (card.below_max === 0) facts.push("Max level");
  else if (card.below_max) facts.push(card.below_max + " below max");
  if (facts.length) tile.append(el("span", facts.join(" · "), "card-facts"));
  return tile;
}

function deckView(deck) {
  const box = el("div", null, "deck-box");
  const s = deck.summary || {};
  const facts = [];
  if (s.avg_elixir !== null && s.avg_elixir !== undefined) facts.push("Average elixir " + s.avg_elixir);
  if (s.cycle !== null && s.cycle !== undefined) facts.push("4-card cycle " + s.cycle);
  if (s.evolutions) facts.push(s.evolutions + (s.evolutions === 1 ? " evolution" : " evolutions"));
  if (s.maxed !== null && s.maxed !== undefined) facts.push(s.maxed + "/8 cards maxed");
  if (facts.length) box.append(el("p", facts.join(" · "), "deck-facts"));
  const list = el("ul", null, "deck");
  for (const card of deck.cards) list.append(cardTile(card));
  box.append(list);
  return box;
}

function playerLine(player) {
  const parts = [player.name || "Unknown", player.tag];
  if (player.clan) parts.push(player.clan);
  if (player.rank) parts.push("rank #" + player.rank);
  else if (player.rating) parts.push("rating " + player.rating);
  return el("p", parts.join(" · "), "player");
}

const TIER_NAMES = { 200: "Top 200", 1000: "Top 1,000", 10000: "Top 10,000" };

function tierTable(m) {
  const table = el("table", null, "tiers");
  const head = el("tr");
  for (const h of ["Players", "Your win rate", "95% range", "Games"]) head.append(el("th", h));
  table.append(el("thead"), el("tbody"));
  table.tHead.append(head);

  function row(name, rate, range, games, mirror) {
    const tr = el("tr");
    tr.append(el("td", name));
    if (mirror) {
      tr.append(el("td", "Mirror"), el("td", "—"));
    } else if (games) {
      tr.append(el("td", pct(rate)), el("td", pct(range[0]) + "–" + pct(range[1])));
    } else {
      tr.append(el("td", "No games yet"), el("td", "—"));
    }
    const cell = el("td", games.toLocaleString());
    if (games && games < 30 && !mirror) cell.append(el("span", " (few)", "few"));
    tr.append(cell);
    table.tBodies[0].append(tr);
  }

  for (const t of m.tiers || []) {
    row(TIER_NAMES[t.tier] || "Top " + t.tier, t.rate, t.range, t.games, t.mirror);
  }
  if (m.record) row("All stored battles", m.record.wins / m.record.games, m.record.range, m.record.games, false);
  else if (m.mirror) row("All stored battles", 0.5, [0.5, 0.5], m.games, true);
  return table;
}

function matchupBox(m, label) {
  const box = el("div", null, "matchup");
  box.append(el("p", label || "Matchup", "label"));
  box.append(el("p", m.archetype_a + "  vs  " + m.archetype_b, "archetypes"));
  box.append(el("p", "Your deck wins about " + pct(m.win_chance), "chance"));
  if (m.tiers || m.record || m.mirror) {
    box.append(tierTable(m));
    if (m.tiers) {
      box.append(el("p", "Tiers only count battles where both players were in that bracket " +
        "when they played. Rates are for your archetype against theirs.", "detail"));
    }
  }
  if (m.note) box.append(el("p", m.note, "note"));
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

async function lookUpMe(event) {
  if (event) event.preventDefault();
  const out = document.getElementById("me-result");
  const tag = document.getElementById("me-tag").value;
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
      el("p", "You " + opp.your_result + " this one.", "detail"),
      el("p", "Their deck", "label"),
      deckView(opp.deck),
    ];
    if (opp.matchup) parts.push(matchupBox(opp.matchup, "Your deck vs theirs"));
    show(out, ...parts);
  } catch (error) {
    show(out, errorBox(error));
  }
}

// --- find an opponent --------------------------------------------------------

async function findOpponent(event) {
  event.preventDefault();
  const out = document.getElementById("find-result");
  const name = document.getElementById("find-name").value;
  let path = "/api/search?name=" + encodeURIComponent(name);
  if (state.me && state.me.rank) path += "&near_rank=" + encodeURIComponent(state.me.rank);
  show(out, el("p", "Searching…", "muted"));
  try {
    const { results } = await api(path);
    if (!results.length) {
      show(out, el("p", "No players found. They may not be in the index yet.", "muted"));
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

async function showOpponentDecks(player, container) {
  const panel = el("div", "Loading their recent decks…", "decks-panel");
  const old = container.querySelector(".decks-panel");
  if (old) old.remove();
  container.append(panel);
  try {
    const { decks } = await api("/api/players/" + tagPath(player.tag) + "/decks");
    if (!decks.length) {
      show(panel, el("p", "No recent ranked battles for this player.", "muted"));
      return;
    }
    const parts = [];
    for (const d of decks) {
      const when = d.last_played ? ", last on " + new Date(d.last_played).toLocaleDateString() : "";
      parts.push(el("p", "Played " + d.games + " times (" + d.wins + " wins" + when + ")", "label"),
        deckView(d.deck));
      if (state.me && state.me.deck.cards.length === 8) {
        try {
          const m = await api("/api/predict", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              deck_a: state.me.deck.cards.map((c) => c.id),
              deck_b: d.deck.cards.map((c) => c.id),
            }),
          });
          parts.push(matchupBox(m, "Your current deck vs this one"));
        } catch (error) {
          parts.push(errorBox(error));
        }
      }
    }
    show(panel, ...parts);
  } catch (error) {
    show(panel, errorBox(error));
  }
}

// --- start -------------------------------------------------------------------

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("me-form").addEventListener("submit", lookUpMe);
  document.getElementById("last-button").addEventListener("click", scoutLast);
  document.getElementById("find-form").addEventListener("submit", findOpponent);
  const saved = storageGet("decklens.myTag");
  if (saved) {
    document.getElementById("me-tag").value = saved;
    lookUpMe();
  }
});
