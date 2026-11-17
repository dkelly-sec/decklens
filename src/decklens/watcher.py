"""Screen watcher: reads your opponent's name and clan off your mirrored phone
screen and sends them to the DeckLens page, which searches their decks.

    pip install -e ".[watcher]"              # once: screen capture + text recognition
    python -m decklens.watcher --check       # see what it reads right now
    python -m decklens.watcher --check --image shot.png   # or what it reads in a screenshot
    python -m decklens.watcher               # run it (Ctrl+C to stop)

It finds Phone Link's mirror window by itself (it's titled after your phone,
e.g. "David's A53"), or an iPad/iPhone mirrored with 5KPlayer's AirPlay
receiver (window "iPad (2)" etc.), and reads that window's own picture, so DeckLens or anything else can sit on top of it; just
don't minimise it. Inside the window it finds the game picture by itself
(cutting away the title bar and grey borders). If that ever goes wrong:
    python -m decklens.watcher --list-windows          # what windows it can see
    python -m decklens.watcher --window "Some title"   # use (and remember) a window
    python -m decklens.watcher --calibrate             # or drag a box by hand, then
    python -m decklens.watcher --manual                # use that box

Then tick "Auto-scout" on the DeckLens page.

How it stays on the right side of the game's rules: the game runs on your
phone as normal; this only reads pixels from the mirror window on your PC
(like a stream capture). It never sends input to the phone or game, never
touches game files, memory or network traffic, and never saves screenshots:
images live in memory for one read and only the recognised text is sent.

What it looks for (checked against real battle screenshots):
- top-right corner: the battle timer ("Time left: 2:54"), so menus and the
  loading screen are ignored;
- top-left corner: the opponent's name, their clan on the line below and,
  in Ranked, their rating on the line below that. The rating (plain digits)
  is the easiest thing to read, so it anchors the layout: the line just above
  it is the clan, the one above that the name. A name OCR can't read (circled
  letters, decorative symbols) is reported as unreadable, never swapped for
  the clan.
"""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import os
import re
import sys
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, NamedTuple, Sequence
from urllib.parse import urlsplit

from .names import read_score, search_key

log = logging.getLogger("decklens.watcher")

CONFIG_FILE = Path.home() / ".decklens" / "watcher.json"
# By default the mirror window is found through Phone Link's or 5KPlayer's
# program (its title is your device's name). A title given with --window is remembered here.
# Regions as fractions of the phone screen: (left, top, right, bottom).
TOP_LEFT = (0.0, 0.0, 0.62, 0.10)     # opponent name and clan
TOP_RIGHT = (0.78, 0.02, 1.0, 0.10)   # "Time left: 2:54" / overtime
BATTLE_TEXT = re.compile(r"\d\s*:\s*\d\d|left|overtime", re.IGNORECASE)
MIN_CONFIDENCE = 0.5
SAME_READ = 0.8  # fuzzy score at which two reads count as the same player
RATING_TEXT = re.compile(r"\d{3,5}")
MAX_RATING = 20_000
# Gap from a line's top to the rating line's top, in rating-line heights: up
# to this, the line sits in the clan slot (seen: 1.7); beyond, the name slot
# (about 3.4). Line heights are used so it works at any window size.
CLAN_SLOT_GAP = 2.5
# "League 3" sits in a taller box than digits, so gaps measured in it are
# smaller (real iPad: clan 1.0, name 2.4; seen elsewhere: clan 1.6, name 3.3).
LEAGUE_CLAN_SLOT_GAP = 2.0
NAME_SLOT_GAP = 5.0
# Game picture height / width: phones are about 2.2 (20:9); an iPad mirrored
# with 5KPlayer is 1.33 (4:3). The same reading areas work for both (checked
# on real screenshots): the game keeps its corner text at the same proportions.
MIN_SHAPE, MAX_SHAPE = 1.2, 2.6
DEFAULT_LINE_HEIGHT = 0.07  # region fraction, when a line's height is unknown
EDGE_INSET = 2  # pixels skipped at each window edge (border lines, rounded corners)


@dataclass(frozen=True)
class TextLine:
    text: str
    x: float  # left edge, as a fraction of the region's width
    y: float  # top edge, as a fraction of the region's height
    confidence: float
    height: float = 0.0  # as a fraction of the region's height (0: unknown)
    width: float = 0.0   # as a fraction of the region's width (0: unknown)


class Sighting(NamedTuple):
    """What was read about the opponent. Any part may be missing: a name
    full of symbols can be unreadable even when the clan and rating aren't."""
    name: str | None
    clan: str | None = None
    rating: int | None = None


# The text reader often returns one line of text as two boxes, split at a space
# or a symbol ("nova | Sub" came back as "nova" and "ISub"). Boxes on the same
# row that nearly touch are one piece of text: at most this many line heights apart.
JOIN_GAP = 1.0


def _is_number(text: str) -> bool:
    return text.replace(" ", "").isdigit()


def _join_rows(boxes: list[tuple[str, float, float, float, float, float]]
               ) -> list[tuple[str, float, float, float, float, float]]:
    """Join boxes (text, left, top, right, bottom, confidence; pixels) that sit side
    by side on one row. Numbers are never joined to anything: the rating must stay
    a clean number, and tower levels beside a name are not part of it."""
    rows: list[tuple[str, float, float, float, float, float]] = []
    for box in sorted(boxes, key=lambda b: b[1]):
        text, left, top, right, bottom, confidence = box
        for i, (t, l, tp, r, bt, c) in enumerate(rows):
            tall, short = max(bottom - top, bt - tp), min(bottom - top, bt - tp)
            overlap = min(bottom, bt) - max(top, tp)
            if (short > 0 and overlap >= 0.5 * short and -0.5 * tall <= left - r <= JOIN_GAP * tall
                    and not _is_number(text) and not _is_number(t)):
                rows[i] = (f"{t} {text}", l, min(top, tp), max(right, r), max(bottom, bt),
                           min(confidence, c))
                break
        else:
            rows.append(box)
    return rows


def lines_from_ocr(result: Any, width: int, height: int) -> list[TextLine]:
    """RapidOCR output [[box, text, confidence], ...] -> TextLines, with boxes on the
    same row joined left to right. Junk is skipped."""
    boxes = []
    for item in result or []:
        try:
            box, text, confidence = item[0], str(item[1]), float(item[2])
            xs, ys = [float(p[0]) for p in box], [float(p[1]) for p in box]
            left, top, right, bottom = min(xs), min(ys), max(xs), max(ys)
        except (TypeError, ValueError, IndexError):
            continue
        if text.strip():
            boxes.append((text.strip(), left, top, right, bottom, confidence))
    w, h = max(width, 1), max(height, 1)
    return [TextLine(text, left / w, top / h, confidence, (bottom - top) / h, (right - left) / w)
            for text, left, top, right, bottom, confidence in _join_rows(boxes)]


def in_battle(top_right: Sequence[TextLine]) -> bool:
    return any(BATTLE_TEXT.search(line.text) for line in top_right)


# What the game may print where a clanless player's clan would go.
NO_CLAN_TEXT = frozenset({"noclan", "notinaclan", "notinclan"})
# Before Ultimate Champion, Ranked shows the player's league ("League 4") where
# the rating would be. It's never their name or clan. (Normalised text; a few
# translations of "league" included.)
LEAGUE_TEXT = re.compile(r"(league|liga|ligue|lega|lig)\d{1,2}")


def _rating(line: TextLine) -> int | None:
    text = line.text.replace(" ", "")
    if not RATING_TEXT.fullmatch(text):
        return None
    value = int(text)
    return value if 100 <= value <= MAX_RATING else None


class Placed(NamedTuple):
    """The lines read_opponent settled on (any may be missing)."""
    name: TextLine | None
    clan: TextLine | None
    rating: TextLine | None   # the rating or the league line


# (name row, clan row): where each line's middle sits, as a fraction of the corner's
# height. Learned from battles where both were read; see Watcher.
Rows = tuple[float, float]


def _middle(line: TextLine) -> float:
    return line.y + line.height / 2


def locate(top_left: Sequence[TextLine], rows: Rows | None = None) -> Placed | None:
    """Which line in the top-left corner is the name, the clan and the rating.

    rows: where the name and clan rows were on earlier battles on this screen. A
    single line with nothing else to go by is the name unless it sits on the clan
    row, so a clan under an unreadable name isn't searched as a name."""
    usable = sorted((ln for ln in top_left if ln.x < 0.35 and ln.confidence >= MIN_CONFIDENCE),
                    key=lambda ln: ln.y)

    def is_league(ln: TextLine) -> bool:
        return bool(LEAGUE_TEXT.fullmatch(search_key(ln.text)))

    # Anything that could be a name or clan. Numbers count: a name can be digits, or
    # be read as digits ("l1ll1" comes back as "11111").
    texts = [ln for ln in usable if not is_league(ln) and len(search_key(ln.text)) >= 1]
    words = [ln for ln in texts if not _is_number(ln.text)]
    # The third line: the rating (Ultimate Champion) or the league ("League 4",
    # before that). Either one pins down where the clan and name lines are.
    anchors = [ln for ln in usable if _rating(ln) is not None or is_league(ln)]

    def is_rating(number: TextLine) -> bool:
        """The rating sits right under the clan, or two lines under the name if
        the clan wasn't read. Other numbers, like tower health just below the
        corner on an iPad, are further down than that: skip them."""
        above = [ln for ln in texts if ln.y < number.y and ln is not number]
        if not above:
            return False
        gap = (number.y - above[-1].y) / (number.height or DEFAULT_LINE_HEIGHT)
        return gap <= (CLAN_SLOT_GAP if len(above) > 1 else NAME_SLOT_GAP)

    rating = next((r for r in anchors if is_rating(r)), None)
    name: TextLine | None = None
    clan: TextLine | None = None
    if rating is not None:
        # Ranked layout: name / clan / rating (or league). Measure from that line,
        # in units of the smallest line's height: "League 3" sits in a taller box
        # than plain digits, which made the name look like it was in the clan slot.
        above = [ln for ln in texts if ln.y < rating.y and ln is not rating]
        floor = (rating.height or DEFAULT_LINE_HEIGHT) / 2   # ignore specks of junk text
        unit = min((ln.height for ln in [rating, *above] if ln.height >= floor),
                   default=rating.height or DEFAULT_LINE_HEIGHT)
        slot = CLAN_SLOT_GAP if _rating(rating) is not None else LEAGUE_CLAN_SLOT_GAP
        near = [ln for ln in above if (rating.y - ln.y) / unit <= NAME_SLOT_GAP]
        if len(near) >= 2 and near[-1].y - near[-2].y >= 0.5 * unit:
            # Two rows above the rating: the nearer is the clan and the next the
            # name, whatever the gaps measure (they vary with how tall the text
            # reader draws its boxes).
            name, clan = near[-2], near[-1]
        elif near:   # one row: the gap says which it is
            if (rating.y - near[-1].y) / unit <= slot:
                clan = near[-1]
            else:
                name = near[-1]
    elif words:
        name = words[0]
        clan = next((ln for ln in words[1:] if 0.1 < ln.y - name.y < 0.5), None)
        if clan is None:
            # A name made of digits sits above the clan like any other name.
            digits = [ln for ln in texts if _is_number(ln.text) and 0.1 < name.y - ln.y < 0.5
                      and abs(ln.x - name.x) < 0.08]
            if digits:
                name, clan = digits[-1], name
            elif rows is not None and abs(_middle(name) - rows[1]) < abs(_middle(name) - rows[0]):
                name, clan = None, name   # on the clan row: the name above it wasn't read
    else:
        return None
    if clan is not None and search_key(clan.text) in NO_CLAN_TEXT:
        clan = None  # the game's "no clan" label, not a clan called that
    return Placed(name, clan, rating) if name or clan else None  # a rating alone says too little


def read_opponent(top_left: Sequence[TextLine], rows: Rows | None = None) -> Sighting | None:
    """Name, clan and rating from the top-left corner of the battle screen."""
    placed = locate(top_left, rows)
    if placed is None:
        return None
    name, clan, rating = placed
    return Sighting(name.text[:40] if name else None, clan.text[:50] if clan else None,
                    _rating(rating) if rating else None)


def describe(found: Sighting) -> str:
    """For logs and --check: Name (Clan) rated 2216."""
    text = found.name or "(name unreadable)"
    text += f" ({found.clan})" if found.clan else " (no clan read)"
    return text + (f", rated {found.rating}" if found.rating else "")


def _same(a: Sighting, b: Sighting) -> bool:
    """Two reads of one opponent: every part both reads have is alike, and they
    share at least one. A part one read missed (the name flickers in and out when
    it is mostly symbols) doesn't make it a different player."""
    def close(x: str, y: str) -> bool:
        kx, ky = search_key(x), search_key(y)
        return read_score(kx, ky) >= SAME_READ and read_score(ky, kx) >= SAME_READ
    shared = [(x, y) for x, y in ((a.name, b.name), (a.clan, b.clan)) if x and y]
    return bool(shared) and all(close(x, y) for x, y in shared)


def _better(a: tuple[Sighting, float], b: tuple[Sighting, float]) -> tuple[Sighting, float]:
    """Of two reads of one opponent, the one to report: the fuller one (name, clan,
    rating), then the one the text reader was surer of."""
    def worth(read: tuple[Sighting, float]) -> tuple[int, float]:
        found, confidence = read
        return (bool(found.name) * 2 + bool(found.clan) + bool(found.rating), confidence)
    return b if worth(b) > worth(a) else a


def _rows_key(image: Any) -> str:
    """Screens of one shape lay the corner out the same way: "2.2" a phone, "1.3" an iPad."""
    h, w = image.shape[:2]
    return f"{h / max(w, 1):.1f}"


def valid_rows(saved: Any) -> dict[str, Rows]:
    """Row positions from the config file, keeping only what looks like them."""
    out: dict[str, Rows] = {}
    for key, pair in (saved.items() if isinstance(saved, dict) else ()):
        if (isinstance(key, str) and isinstance(pair, (list, tuple)) and len(pair) == 2
                and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in pair)
                and 0 <= pair[0] < pair[1] <= 1.5):
            out[key[:8]] = (float(pair[0]), float(pair[1]))
    return out


def phone_area(image: Any) -> Any:
    """The game picture inside a mirror window (no toolbar, borders or letterboxing).

    The window's edge colour is taken as background; rows and columns that are
    mostly background (borders, toolbars with a few icons) are cut away and the
    largest solid block left is the phone screen. If no edge is a plain colour
    (the game fills the window) or the result isn't phone-shaped, the whole
    image is used.
    """
    import numpy as np

    h, w = image.shape[:2]
    if h < 50 or w < 50:
        return image
    # Background = the colour of whichever window edges are plain. The game may
    # touch every edge but one: Phone Link's "Phone screen" window is as narrow
    # as the game, which reaches its bottom, so only the title bar is plain. Each
    # edge is sampled a few pixels in, past the 1-px border line and rounded
    # corners some windows draw: otherwise the title bar stays in, and its text
    # ("Phone screen") is read as the opponent's name.
    inset = EDGE_INSET
    edges = [image[inset:inset + 3].reshape(-1, 3), image[-inset - 3:-inset].reshape(-1, 3),
             image[:, inset:inset + 3].reshape(-1, 3), image[:, -inset - 3:-inset].reshape(-1, 3)]
    plain = []
    for edge in edges:
        edge = edge.astype(int)
        colour = np.median(edge, axis=0)
        if (np.abs(edge - colour).sum(axis=1) < 30).mean() >= 0.9:
            plain.append(colour)
    if not plain:
        return image  # no plain frame: the game fills the window
    # Background is any plain edge's colour, not their average: a grey title bar
    # over a game that fades to black at the bottom would match neither.
    pixels = image.astype(np.int16)
    content = np.ones((h, w), dtype=bool)
    for colour in plain:
        content &= np.abs(pixels - colour).sum(axis=2) > 40

    def longest_run(flags: Any, gap: int = 0) -> tuple[int, int]:
        """Longest stretch of True, bridging gaps of up to `gap` False values."""
        best, start, best_span, last_on = (0, 0), None, -1, None
        for i, on in enumerate(list(flags) + [False] * (gap + 1)):
            if on:
                if start is None or (last_on is not None and i - last_on - 1 > gap):
                    start = i
                last_on = i
            elif start is not None and last_on is not None and i - last_on > gap:
                if last_on + 1 - start > best_span:
                    best, best_span = (start, last_on + 1), last_on + 1 - start
                start = None
        return best

    # Columns first: the phone picture runs most of the window's height, even
    # when it's only a quarter of its width (a wide window). Then rows within
    # those columns, then the columns again within those rows.
    left, right = longest_run(content.mean(axis=0) > 0.5)
    if right - left < 0.1 * w:
        return image
    # A game row is never mostly the window's flat background colour, while a
    # border or toolbar row is; some arena rows are close to grey, so the bar
    # is low and small gaps are bridged.
    inside = content[:, left:right].mean(axis=1)
    outside_cols = np.concatenate([content[:, :left], content[:, right:]], axis=1)
    outside = outside_cols.mean(axis=1) if outside_cols.shape[1] else np.zeros(h)
    # ...and a toolbar row spans the whole window, so it's busy outside too.
    top, bottom = longest_run((inside > 0.15) & (outside < 0.3), gap=max(1, h // 100))
    if bottom - top < 0.3 * h:
        return image
    left, right = longest_run(content[top:bottom].mean(axis=0) > 0.5, gap=max(1, w // 200))
    found = image[top:bottom, left:right]
    ratio = found.shape[0] / max(found.shape[1], 1)
    return found if MIN_SHAPE <= ratio <= MAX_SHAPE else image


def _screen_reader(mss: Any) -> Any:
    """mss's screen grabber (mss.MSS in newer versions; mss.mss is deprecated)."""
    return (getattr(mss, "MSS", None) or mss.mss)()


def enlarge(part: Any, min_width: int = 300) -> Any:
    """Small mirror windows give tiny text; scale it up smoothly before reading.
    (OpenCV comes with the text recognition package; without it, leave as is.)"""
    h, w = part.shape[:2]
    if w == 0 or w >= min_width:
        return part
    try:
        import cv2
    except ImportError:
        return part
    scale = min_width / w
    return cv2.resize(part, (min_width, max(1, round(h * scale))), interpolation=cv2.INTER_CUBIC)


def crop(image: Any, region: tuple[float, float, float, float]) -> Any:
    h, w = image.shape[:2]
    left, top, right, bottom = region
    return image[int(top * h):int(bottom * h), int(left * w):int(right * w)]


UNREADABLE_AFTER = 5  # looks in a battle without a readable name or clan


class Watcher:
    """Reports each opponent once per battle, after two reads agree.

    If a battle goes on but nothing in the opponent's corner can be read
    (names in scripts the text reader doesn't know, like Arabic), it says so
    once ("unreadable") and again when that battle ends ("ended"), so the page
    can show the opponent from your battle log instead."""

    def __init__(self, grab: Callable[[], Any], ocr: Callable[[Any], Any],
                 send: Callable[[Sighting], None], *,
                 event: Callable[[str], None] | None = None,
                 rescan: Callable[[], bool] | None = None,
                 confirm: int = 2, rearm_after: int = 3, unreadable_after: int = UNREADABLE_AFTER,
                 rows: dict[str, Rows] | None = None,
                 save_rows: Callable[[dict[str, Rows]], None] | None = None):
        self.grab, self.ocr, self.send, self.event = grab, ocr, send, event
        self.rescan = rescan  # True when the page asked to read this battle again
        self.confirm, self.rearm_after = confirm, rearm_after
        self.unreadable_after = unreadable_after
        # Where the name and clan rows sit, per screen shape, learned whenever both
        # are read (and remembered between runs through save_rows).
        self.rows: dict[str, Rows] = dict(rows or {})
        self.save_rows = save_rows
        self.reported = False
        self.pending: Sighting | None = None
        self.pending_confidence = 0.0
        self.agree = 0
        self.misses = 0
        self.unread = 0          # looks in this battle with nothing readable
        self.flagged = False     # said "unreadable" for this battle

    def _tell(self, kind: str) -> None:
        if self.event is None:
            return
        try:
            self.event(kind)
        except OSError as exc:  # only a hint for the page: never stop watching over it
            log.warning("couldn't tell DeckLens the battle %s: %s", kind, type(exc).__name__)

    def _read(self, image: Any, region: tuple[float, float, float, float]) -> list[TextLine]:
        part = enlarge(crop(image, region))
        h, w = part.shape[:2]
        return lines_from_ocr(self.ocr(part), w, h)

    def step(self) -> Sighting | None:
        """One look at the screen. Returns the opponent if they were just reported."""
        if self.rescan is not None and self.rescan():
            self.reported, self.pending, self.agree = False, None, 0
            self.unread, self.flagged = 0, False
        image = self.grab()  # None: the window isn't available right now
        if image is None or not in_battle(self._read(image, TOP_RIGHT)):
            self.misses += 1
            if self.misses >= self.rearm_after:  # battle over: ready for the next one
                if self.flagged:
                    self._tell("ended")
                self.reported, self.pending, self.agree = False, None, 0
                self.unread, self.flagged = 0, False
            return None
        self.misses = 0
        if self.reported or self.flagged:
            return None
        shape = _rows_key(image)
        placed = locate(self._read(image, TOP_LEFT), self.rows.get(shape))
        if placed is None:
            self.unread += 1
            if self.unread >= self.unreadable_after:
                self._tell("unreadable")
                self.flagged = True
            return None
        self.unread = 0
        self._learn_rows(shape, placed)
        found = Sighting(placed.name.text[:40] if placed.name else None,
                         placed.clan.text[:50] if placed.clan else None,
                         _rating(placed.rating) if placed.rating else None)
        confidence = (placed.name or placed.clan).confidence
        if self.pending is not None and _same(found, self.pending):
            self.agree += 1
            # Looks at one opponent differ a little (the picture is a video stream):
            # report the fullest, surest of them, not whichever came last.
            self.pending, self.pending_confidence = _better(
                (self.pending, self.pending_confidence), (found, confidence))
        else:
            self.pending, self.pending_confidence, self.agree = found, confidence, 1
        if self.agree >= self.confirm:
            self.send(self.pending)
            self.reported = True
            return self.pending
        return None

    def _learn_rows(self, shape: str, placed: Placed) -> None:
        if placed.name is None or placed.clan is None:
            return
        rows = (round(_middle(placed.name), 3), round(_middle(placed.clan), 3))
        old = self.rows.get(shape)
        if rows[0] >= rows[1] or (old and max(abs(rows[0] - old[0]), abs(rows[1] - old[1])) < 0.03):
            return  # not two rows, or where they already were
        self.rows[shape] = rows
        if self.save_rows is not None:
            try:
                self.save_rows(dict(self.rows))
            except OSError as exc:  # can't write the config file: still known for this run
                log.debug("couldn't remember the row positions: %s", type(exc).__name__)


# --- talking to DeckLens ---------------------------------------------------------


class NeedLogin(Exception):
    pass


class Heartbeat:
    """Sends the watcher's status: straight away when it changes, otherwise
    every few seconds so the page knows the watcher is still running."""

    def __init__(self, send: Callable[[str, str | None], None], *, every: float = 5.0,
                 clock: Callable[[], float] = time.monotonic):
        self.send, self.every, self.clock = send, every, clock
        self.last: tuple[str, str | None] | None = None
        self.sent_at = float("-inf")
        self.rescan_asked = False

    def __call__(self, state: str, detail: str | None = None) -> None:
        now = self.clock()
        if (state, detail) == self.last and now - self.sent_at < self.every:
            return
        try:
            if self.send(state, detail):
                self.rescan_asked = True
        except (OSError, NeedLogin) as exc:  # the next beat will try again
            log.debug("couldn't send the watcher status: %s", type(exc).__name__)
            return
        self.last, self.sent_at = (state, detail), now

    def take_rescan(self) -> bool:
        """Whether the page asked to read the opponent again (once per ask)."""
        asked, self.rescan_asked = self.rescan_asked, False
        return asked


class DeckLensClient:
    """Logs in like the page does and posts what the watcher read (text only)."""

    def __init__(self, base_url: str, session: Any = None):
        self.base = self.server_base(base_url)
        if session is None:
            import requests
            session = requests.Session()
        self.session = session
        # The server's cross-site check wants requests to come from its own origin.
        self.session.headers.update({"Origin": self.base})

    @staticmethod
    def server_base(url: str) -> str:
        """scheme://host[:port] of the DeckLens server, rebuilt from its parts.

        The password goes here, so anything that URL parsers read differently is
        refused: a backslash ("http://evil\\@127.0.0.1" is 127.0.0.1 to urlsplit but
        evil to requests), a user name or "@", spaces or control characters. Plain
        http is only for this computer."""
        if not isinstance(url, str) or "\\" in url or any(c <= " " or c == "\x7f" for c in url):
            raise ValueError("that server address isn't a plain URL")
        parts = urlsplit(url)
        try:
            host, port = parts.hostname, parts.port
        except ValueError:
            raise ValueError("that server address has a bad port") from None
        if parts.scheme not in ("http", "https") or not host or "@" in parts.netloc:
            raise ValueError("use https://host or http://127.0.0.1:port for the DeckLens server")
        if parts.scheme == "http" and host not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("use https:// for a DeckLens server that isn't on this computer")
        shown = f"[{host}]" if ":" in host else host
        return f"{parts.scheme}://{shown}" + (f":{port}" if port is not None else "")

    def _post(self, path: str, body: dict[str, Any], timeout: float = 10) -> Any:
        # Never follow redirects: requests would re-send the body (password included)
        # to wherever a 307/308 points, even plain http.
        return self.session.post(self.base + path, json=body, timeout=timeout,
                                 allow_redirects=False)

    def login(self, username: str, password: str) -> None:
        r = self._post("/api/login", {"username": username, "password": password})
        if r.status_code != 200:
            detail = r.json().get("detail") if r.headers.get("content-type", "").startswith(
                "application/json") else None
            raise NeedLogin(detail or f"login failed (HTTP {r.status_code})")

    def event(self, kind: str) -> None:
        """A battle whose opponent can't be read ("unreadable"), or its end ("ended")."""
        r = self._post("/api/watch", {"kind": kind})
        if r.status_code == 401:
            raise NeedLogin("session expired")
        r.raise_for_status()

    def status(self, state: str, detail: str | None) -> bool:
        """Send the watcher's state. True if the page asked to read the opponent again."""
        r = self._post("/api/watch/status",
                       {"state": state, "detail": (detail or None) and detail[:80]}, timeout=5)
        if r.status_code == 401:
            raise NeedLogin("session expired")
        r.raise_for_status()
        try:
            return r.json().get("rescan") is True
        except ValueError:
            return False

    def report(self, found: Sighting) -> None:
        r = self._post("/api/watch", {"name": found.name, "clan": found.clan,
                                      "rating": found.rating})
        if r.status_code == 401:
            raise NeedLogin("session expired")
        r.raise_for_status()


# --- screen capture, text recognition, calibration (Windows PC) ------------------


def _dpi_aware() -> None:
    """Use real pixels on scaled Windows displays, so boxes line up."""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError) as exc:  # older Windows: boxes may be off on scaled screens
            log.debug("couldn't enable DPI awareness: %s", exc)


def read_config() -> dict[str, Any]:
    try:
        config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return config if isinstance(config, dict) else {}


def write_config(**changes: Any) -> None:
    config = {**read_config(), **changes}
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config), encoding="utf-8")


def load_region() -> dict[str, int]:
    region = read_config()
    if all(isinstance(region.get(k), int) for k in ("left", "top", "width", "height")):
        return {k: region[k] for k in ("left", "top", "width", "height")}
    raise SystemExit("No box saved yet: run  python -m decklens.watcher --calibrate")


def screen_grabber(region: dict[str, int]) -> Callable[[], Any]:
    """Reads a fixed box of the screen (manual mode): whatever is on top there."""
    import mss
    import numpy as np

    sct = _screen_reader(mss)

    def grab() -> Any:
        shot = np.asarray(sct.grab(region))     # BGRA, in memory only
        return phone_area(shot[:, :, 2::-1].copy())
    return grab


# What the watcher can see, shown on the DeckLens page as connected / not connected.
STATES = ("connected", "screen_only", "no_window", "minimised", "starting", "unavailable")
Status = Callable[[str, "str | None"], None]


def window_grabber(titles: list[str], on_status: Status | None = None) -> Callable[[], Any]:
    """Reads the Phone Link window's own picture (works with other windows on
    top), then cuts out the game picture inside it. Returns None while the
    window is closed or minimised. on_status(state, detail) hears what it saw."""
    import numpy as np

    from . import wincapture

    state: dict[str, Any] = {"window": None, "said": None, "screen": None}

    def report(status: str, detail: str | None, message: str) -> None:
        if on_status is not None:
            on_status(status, detail)
        if state["said"] != message:  # log each change once, not every second
            state["said"] = message
            log.info(message)

    def read_screen(box: tuple[int, int, int, int]) -> Any:
        if state["screen"] is None:
            import mss
            state["screen"] = _screen_reader(mss)
        left, top, width, height = box
        shot = np.asarray(state["screen"].grab({"left": left, "top": top,
                                                "width": width, "height": height}))
        return shot[:, :, 2::-1].copy()

    def grab() -> Any:
        window = state["window"] or wincapture.find_window(titles)
        if window is None:
            wanted = (" or ".join(repr(t) for t in titles) if titles
                      else "a mirrored phone (Phone Link) or iPad (5KPlayer)")
            report("no_window", None, f"waiting for {wanted}: open the phone screen in "
                   "Phone Link, or mirror your iPad to 5KPlayer "
                   "(python -m decklens.watcher --list-windows shows what it can see)")
            return None
        image = wincapture.capture(window)
        if image is None:
            box = wincapture.client_box(window)
            if box is None:
                state["window"] = wincapture.find_window(titles)  # closed or minimised
                report("minimised", window.title, "the mirror window is minimised or "
                       "closed: restore it (it can sit behind other windows)")
                return None
            # Windows wouldn't draw it for us: read that part of the screen instead.
            report("screen_only", window.title, "reading the mirror window from the "
                   "screen: keep it visible (not covered) while you play")
            image = read_screen(box)
        else:
            report("connected", window.title,
                   f"reading the window {window.title!r} (it can sit behind other windows)")
        state["window"] = window
        return phone_area(image)
    return grab


# The main text reader (RapidOCR's PP-OCRv4) knows Latin letters and Chinese. It reads
# Latin names best, but writes Japanese kanji in their simplified Chinese forms
# (単騎 -> 单骑) and can't read kana: most kana names came back as nothing, ミナ as
# 三十. A second, multilingual reader (PP-OCRv6: simplified and traditional Chinese,
# Japanese, Latin; bundled with the rapidocr package) re-reads each piece of text the
# main one found. Its reading is taken when the main one's was in Chinese characters
# or unsure, or when it reads Chinese or Japanese itself. Numbers aren't re-read.
# On rendered battle corners: Japanese names read right 2 -> 47 of 48, traditional
# Chinese 7 -> 20 of 20, Latin unchanged (39 of 40). About 0.1 s more per look.
REREAD_PAD = 0.15  # around each piece of text, in its heights


def has_han_or_kana(text: str) -> bool:
    """Chinese characters (also used in Japanese) or Japanese kana in text."""
    return any(unicodedata.name(ch, "").startswith(
        ("CJK ", "HIRAGANA ", "KATAKANA ", "HALFWIDTH KATAKANA ")) for ch in text)


def reread(image: Any, result: Any, recognise: Callable[[Any], tuple[str, float]]) -> list[Any]:
    """The main reader's [[box, text, confidence], ...] (all of it, sure or not) with
    each piece re-read by recognise where that helps (see above). Pieces neither
    reader is sure of are dropped, as the main reader alone drops them."""
    h, w = image.shape[:2]
    out = []
    for item in result or []:
        try:
            box, text, confidence = item[0], str(item[1]), float(item[2])
            xs, ys = [float(p[0]) for p in box], [float(p[1]) for p in box]
        except (TypeError, ValueError, IndexError):
            continue
        if not (_is_number(text) and confidence >= MIN_CONFIDENCE):
            pad = REREAD_PAD * (max(ys) - min(ys))
            part = image[max(0, int(min(ys) - pad)):min(h, int(max(ys) + pad) + 1),
                         max(0, int(min(xs) - pad)):min(w, int(max(xs) + pad) + 1)]
            again, sure = recognise(part) if part.size else ("", 0.0)
            if again.strip() and sure >= MIN_CONFIDENCE and (
                    has_han_or_kana(text) or confidence < MIN_CONFIDENCE or has_han_or_kana(again)):
                text, confidence = again.strip(), sure
        if confidence >= MIN_CONFIDENCE:
            out.append([box, text, confidence])
    return out


def multilingual_reader() -> Callable[[Any], tuple[str, float]] | None:
    """Reads one piece of text (no searching for it) with the multilingual model, or
    None if the rapidocr package isn't installed."""
    try:
        from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR
    except ImportError:
        log.warning("Chinese and Japanese names need the multilingual text reader: "
                    'run  pip install -e ".[watcher]"  again')
        return None
    # The model shipped inside the package, so nothing is downloaded.
    engine = RapidOCR(params={"Global.log_level": "error", "Rec.ocr_version": OCRVersion.PPOCRV6,
                              "Rec.model_type": ModelType.SMALL, "Rec.lang_type": LangRec.JAPAN})

    def recognise(part: Any) -> tuple[str, float]:
        found = engine(part, use_det=False, use_cls=False, use_rec=True)
        texts, scores = getattr(found, "txts", None), getattr(found, "scores", None)
        return (str(texts[0]), float(scores[0])) if texts and scores else ("", 0.0)
    return recognise


def text_reader() -> Callable[[Any], Any]:
    from rapidocr_onnxruntime import RapidOCR

    engine = RapidOCR()
    recognise = multilingual_reader()
    if recognise is None:
        return lambda image: engine(image)[0]
    return lambda image: reread(image, engine(image, text_score=0.0)[0], recognise)


def calibrate() -> None:
    """Drag a box around the phone screen in the Phone Link window."""
    import tkinter as tk

    picked: dict[str, int] = {}
    root = tk.Tk()
    root.attributes("-fullscreen", True)
    root.attributes("-alpha", 0.3)
    root.attributes("-topmost", True)
    canvas = tk.Canvas(root, cursor="cross", bg="black", highlightthickness=0)
    canvas.pack(fill="both", expand=True)
    canvas.create_text(root.winfo_screenwidth() // 2, 40, fill="white", font=("Segoe UI", 18),
                       text="Drag a box around the phone screen (just the game, no window "
                            "borders). Esc to cancel.")
    start: dict[str, int] = {}
    rect = [None]

    def down(e: Any) -> None:
        start.update(x=e.x_root, y=e.y_root)
        rect[0] = canvas.create_rectangle(e.x, e.y, e.x, e.y, outline="red", width=3)

    def drag(e: Any) -> None:
        if rect[0] is not None:
            canvas.coords(rect[0], start["x"] - root.winfo_rootx(), start["y"] - root.winfo_rooty(),
                          e.x, e.y)

    def up(e: Any) -> None:
        left, top = min(start["x"], e.x_root), min(start["y"], e.y_root)
        width, height = abs(e.x_root - start["x"]), abs(e.y_root - start["y"])
        if width > 50 and height > 100:
            picked.update(left=left, top=top, width=width, height=height)
        root.destroy()

    canvas.bind("<ButtonPress-1>", down)
    canvas.bind("<B1-Motion>", drag)
    canvas.bind("<ButtonRelease-1>", up)
    root.bind("<Escape>", lambda e: root.destroy())
    root.mainloop()
    if not picked:
        raise SystemExit("Cancelled: nothing saved.")
    write_config(**picked)
    ratio = picked["height"] / picked["width"]
    print(f"Saved the phone screen: {picked['width']}x{picked['height']} at "
          f"({picked['left']}, {picked['top']}).")
    if not 1.8 <= ratio <= 2.4:
        print("That box isn't phone-shaped (expected about 2:1 tall). If reads fail, "
              "calibrate again around just the game picture.")


def check(grab: Callable[[], Any]) -> None:
    """One read of the screen, to confirm everything lines up."""
    image = grab()
    if image is None:
        print("Couldn't see the game: is Phone Link (or 5KPlayer) open, not minimised?")
        return
    height, width = image.shape[:2]
    print(f"Game picture: {width}x{height} pixels"
          + ("" if MIN_SHAPE <= height / max(width, 1) <= MAX_SHAPE else
             "  (not phone- or tablet-shaped: try --list-windows / --window, or --calibrate)"))
    if width < 300:
        print("That's small: make the mirror window bigger for reliable reading.")
    ocr = text_reader()
    top_right = lines_from_ocr(ocr(enlarge(crop(image, TOP_RIGHT))),
                               *enlarge(crop(image, TOP_RIGHT)).shape[1::-1])
    top_left = lines_from_ocr(ocr(enlarge(crop(image, TOP_LEFT))),
                              *enlarge(crop(image, TOP_LEFT)).shape[1::-1])
    def shown(found_lines: Sequence[TextLine]) -> list[str]:
        # with how sure the reader was: under 50% a line is ignored
        return [f"{ln.text} ({ln.confidence:.0%})" for ln in found_lines]

    print("Top right:", shown(top_right))
    print("Top left: ", shown(top_left))
    print("In a battle:", "yes" if in_battle(top_right) else "no (start a match and check again)")
    rows = valid_rows(read_config().get("rows")).get(_rows_key(image))
    found = read_opponent(top_left, rows)
    print(f"Opponent: {describe(found)}" if found else "Opponent: nothing readable in their corner")


def image_grabber(path: str) -> Callable[[], Any]:
    """A saved screenshot (of the phone, or of the mirror window) in place of the
    live window, so a read that went wrong can be looked at again with --check."""
    import cv2

    picture = cv2.imread(path)  # BGR
    if picture is None:
        raise SystemExit(f"Couldn't open {path} as a picture.")
    picture = picture[:, :, ::-1].copy()
    return lambda: phone_area(picture)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read your opponent off your mirrored phone.")
    parser.add_argument("--check", action="store_true", help="read the screen once and show it")
    parser.add_argument("--image", metavar="FILE",
                        help="with --check: read a saved screenshot instead of the live window")
    parser.add_argument("--window", metavar="TITLE",
                        help="title of the mirror window, remembered for next time "
                             "(default: found through Phone Link)")
    parser.add_argument("--auto-window", action="store_true",
                        help="forget a remembered --window title and find Phone Link again")
    parser.add_argument("--list-windows", action="store_true",
                        help="show window titles and their programs")
    parser.add_argument("--calibrate", action="store_true",
                        help="fallback: drag a box around the phone screen by hand")
    parser.add_argument("--manual", action="store_true", help="use the box from --calibrate")
    parser.add_argument("--url", default=os.environ.get("DECKLENS_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--user", default=os.environ.get("DECKLENS_USER"))
    parser.add_argument("--interval", type=float, default=1.0, help="seconds between looks")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    _dpi_aware()

    if args.list_windows:
        from . import wincapture
        chosen = wincapture.find_window([read_config().get("window")] if
                                        read_config().get("window") else [])
        for window in wincapture.list_windows():
            mark = "  <- the watcher uses this one" if chosen and window.handle == chosen.handle else ""
            print(f"{window.title}   [{window.process or '?'}]{mark}")
        return 0
    if args.auto_window:
        write_config(window=None)
    if args.window:
        write_config(window=args.window)
        print(f"Using (and remembering) the window {args.window!r}.")
    remembered = read_config().get("window")
    titles = [remembered] if isinstance(remembered, str) and remembered else []
    if args.calibrate:
        calibrate()
        return 0
    if args.image and not args.check:
        parser.error("--image goes with --check")
    grab = (image_grabber(args.image) if args.image else
            screen_grabber(load_region()) if args.manual else window_grabber(titles))
    if args.check:
        check(grab)
        return 0
    if not 0.3 <= args.interval <= 10:
        parser.error("--interval must be 0.3-10 seconds")

    client = DeckLensClient(args.url)
    username = args.user or input("DeckLens username: ").strip()

    def log_in() -> None:
        while True:
            try:
                client.login(username, getpass.getpass("DeckLens password: "))
                return
            except NeedLogin as exc:
                print(f"Couldn't log in: {exc}")

    log_in()

    def send(found: Sighting) -> None:
        try:
            client.report(found)
        except NeedLogin:
            print("DeckLens logged you out; log in again.")
            log_in()
            client.report(found)
        log.info("opponent: %s -> sent to DeckLens", describe(found))

    heartbeat = Heartbeat(client.status)
    heartbeat("starting")
    if args.manual:
        box_grab = grab

        def grab() -> Any:  # the dragged box is always "there": say so on each look
            heartbeat("connected", "your calibrated box")
            return box_grab()
    else:
        grab = window_grabber(titles, on_status=heartbeat)
    def event(kind: str) -> None:
        try:
            client.event(kind)
        except NeedLogin:
            print("DeckLens logged you out; log in again.")
            log_in()
            client.event(kind)
        log.info("battle %s -> told DeckLens", kind)

    watcher = Watcher(grab, text_reader(), send, event=event, rescan=heartbeat.take_rescan,
                      rows=valid_rows(read_config().get("rows")),
                      save_rows=lambda rows: write_config(rows=rows))
    log.info("watching your phone screen (Ctrl+C to stop). Tick Auto-scout on the DeckLens page.")
    try:
        while True:
            started = time.monotonic()
            try:
                watcher.step()
            except OSError as exc:  # network blip talking to DeckLens: keep watching
                log.warning("couldn't reach DeckLens: %s", type(exc).__name__)
            time.sleep(max(0.0, args.interval - (time.monotonic() - started)))
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
