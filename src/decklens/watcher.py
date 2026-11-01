"""Screen watcher: reads your opponent's name and clan off your mirrored phone
screen and sends them to the DeckLens page, which searches their decks.

    pip install -e ".[watcher]"              # once: screen capture + text recognition
    python -m decklens.watcher --check       # see what it reads right now
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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, NamedTuple, Sequence
from urllib.parse import urlsplit

from .names import fuzzy_score, search_key

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
NAME_SLOT_GAP = 5.0
# Game picture height / width: phones are about 2.2 (20:9); an iPad mirrored
# with 5KPlayer is 1.33 (4:3). The same reading areas work for both (checked
# on real screenshots): the game keeps its corner text at the same proportions.
MIN_SHAPE, MAX_SHAPE = 1.2, 2.6
DEFAULT_LINE_HEIGHT = 0.07  # region fraction, when a line's height is unknown


@dataclass(frozen=True)
class TextLine:
    text: str
    x: float  # left edge, as a fraction of the region's width
    y: float  # top edge, as a fraction of the region's height
    confidence: float
    height: float = 0.0  # as a fraction of the region's height (0: unknown)


class Sighting(NamedTuple):
    """What was read about the opponent. Any part may be missing: a name
    full of symbols can be unreadable even when the clan and rating aren't."""
    name: str | None
    clan: str | None = None
    rating: int | None = None


def lines_from_ocr(result: Any, width: int, height: int) -> list[TextLine]:
    """RapidOCR output [[box, text, confidence], ...] -> TextLines. Junk is skipped."""
    lines = []
    for item in result or []:
        try:
            box, text, confidence = item[0], str(item[1]), float(item[2])
            x = min(float(p[0]) for p in box) / max(width, 1)
            y = min(float(p[1]) for p in box) / max(height, 1)
            tall = (max(float(p[1]) for p in box) - min(float(p[1]) for p in box)) / max(height, 1)
        except (TypeError, ValueError, IndexError):
            continue
        if text.strip():
            lines.append(TextLine(text.strip(), x, y, confidence, tall))
    return lines


def in_battle(top_right: Sequence[TextLine]) -> bool:
    return any(BATTLE_TEXT.search(line.text) for line in top_right)


# What the game may print where a clanless player's clan would go.
NO_CLAN_TEXT = frozenset({"noclan", "notinaclan", "notinclan"})


def _rating(line: TextLine) -> int | None:
    text = line.text.replace(" ", "")
    if not RATING_TEXT.fullmatch(text):
        return None
    value = int(text)
    return value if 100 <= value <= MAX_RATING else None


def read_opponent(top_left: Sequence[TextLine]) -> Sighting | None:
    """Name, clan and rating from the top-left corner of the battle screen."""
    usable = [ln for ln in top_left if ln.x < 0.35 and ln.confidence >= MIN_CONFIDENCE]
    ratings = sorted((ln for ln in usable if _rating(ln) is not None), key=lambda ln: ln.y)
    words = sorted((ln for ln in usable if not ln.text.replace(" ", "").isdigit()
                    and len(search_key(ln.text)) >= 1), key=lambda ln: ln.y)
    name: TextLine | None
    clan: TextLine | None
    def is_rating(number: TextLine) -> bool:
        """The rating sits right under the clan, or two lines under the name if
        the clan wasn't read. Other numbers, like tower health just below the
        corner on an iPad, are further down than that: skip them."""
        above = [ln for ln in words if ln.y < number.y]
        if not above:
            return False
        gap = (number.y - above[-1].y) / (number.height or DEFAULT_LINE_HEIGHT)
        return gap <= (CLAN_SLOT_GAP if len(above) > 1 else NAME_SLOT_GAP)

    rating = next((r for r in ratings if is_rating(r)), None)
    words = [ln for ln in words if rating is None or ln.y < rating.y]
    if rating is not None:
        # Ranked layout: name / clan / rating. Measure from the rating line.
        unit = rating.height or DEFAULT_LINE_HEIGHT
        above = [ln for ln in words if ln.y < rating.y]
        clan = next((ln for ln in reversed(above) if (rating.y - ln.y) / unit <= CLAN_SLOT_GAP),
                    None)
        name = next((ln for ln in reversed(above)
                     if CLAN_SLOT_GAP < (rating.y - ln.y) / unit <= NAME_SLOT_GAP), None)
    elif words:
        name = words[0]
        clan = next((ln for ln in words[1:] if 0.1 < ln.y - name.y < 0.5), None)
    else:
        return None
    if clan is not None and search_key(clan.text) in NO_CLAN_TEXT:
        clan = None  # the game's "no clan" label, not a clan called that
    found = Sighting(name.text[:40] if name else None, clan.text[:50] if clan else None,
                     _rating(rating) if rating else None)
    return found if found.name or found.clan else None  # a rating alone says too little


def describe(found: Sighting) -> str:
    """For logs and --check: Name (Clan) rated 2216."""
    text = found.name or "(name unreadable)"
    text += f" ({found.clan})" if found.clan else " (no clan read)"
    return text + (f", rated {found.rating}" if found.rating else "")


def _same(a: Sighting, b: Sighting) -> bool:
    def close(x: str | None, y: str | None) -> bool:
        if not x or not y:
            return x == y
        kx, ky = search_key(x), search_key(y)
        return fuzzy_score(kx, ky) >= SAME_READ and fuzzy_score(ky, kx) >= SAME_READ
    return close(a.name, b.name) and close(a.clan, b.clan)


def phone_area(image: Any) -> Any:
    """The game picture inside a mirror window (no toolbar, borders or letterboxing).

    The window's edge colour is taken as background; rows and columns that are
    mostly background (borders, toolbars with a few icons) are cut away and the
    largest solid block left is the phone screen. If the edges aren't a plain
    colour (the game fills the window) or the result isn't phone-shaped, the
    whole image is used.
    """
    import numpy as np

    h, w = image.shape[:2]
    if h < 50 or w < 50:
        return image
    # Background = the colour of whichever window edges are plain. The game may
    # touch some edges (e.g. top and bottom in a tall window), but the frame
    # should show on at least two.
    edges = [image[:3].reshape(-1, 3), image[-3:].reshape(-1, 3),
             image[:, :3].reshape(-1, 3), image[:, -3:].reshape(-1, 3)]
    plain = []
    for edge in edges:
        edge = edge.astype(int)
        colour = np.median(edge, axis=0)
        if (np.abs(edge - colour).sum(axis=1) < 30).mean() >= 0.9:
            plain.append(colour)
    if len(plain) < 2:
        return image  # no plain frame: the game fills the window
    background = np.median(np.array(plain), axis=0)
    content = np.abs(image.astype(np.int16) - background).sum(axis=2) > 40

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
                 confirm: int = 2, rearm_after: int = 3, unreadable_after: int = UNREADABLE_AFTER):
        self.grab, self.ocr, self.send, self.event = grab, ocr, send, event
        self.confirm, self.rearm_after = confirm, rearm_after
        self.unreadable_after = unreadable_after
        self.reported = False
        self.pending: Sighting | None = None
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
        found = read_opponent(self._read(image, TOP_LEFT))
        if found is None:
            self.unread += 1
            if self.unread >= self.unreadable_after:
                self._tell("unreadable")
                self.flagged = True
            return None
        self.unread = 0
        if self.pending is not None and _same(found, self.pending):
            self.agree += 1
        else:
            self.pending, self.agree = found, 1
        if self.agree >= self.confirm:
            self.send(found)
            self.reported = True
            return found
        return None


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

    def __call__(self, state: str, detail: str | None = None) -> None:
        now = self.clock()
        if (state, detail) == self.last and now - self.sent_at < self.every:
            return
        try:
            self.send(state, detail)
        except (OSError, NeedLogin) as exc:  # the next beat will try again
            log.debug("couldn't send the watcher status: %s", type(exc).__name__)
            return
        self.last, self.sent_at = (state, detail), now


class DeckLensClient:
    """Logs in like the page does and posts what the watcher read (text only)."""

    def __init__(self, base_url: str, session: Any = None):
        parts = urlsplit(base_url)
        local = parts.hostname in ("127.0.0.1", "localhost", "::1")
        if parts.scheme not in ("http", "https") or (parts.scheme == "http" and not local):
            raise ValueError("use https:// for a DeckLens server that isn't on this computer")
        self.base = f"{parts.scheme}://{parts.netloc}"
        if session is None:
            import requests
            session = requests.Session()
        self.session = session
        # The server's cross-site check wants requests to come from its own origin.
        self.session.headers.update({"Origin": self.base})

    def login(self, username: str, password: str) -> None:
        r = self.session.post(self.base + "/api/login", timeout=10,
                              json={"username": username, "password": password})
        if r.status_code != 200:
            detail = r.json().get("detail") if r.headers.get("content-type", "").startswith(
                "application/json") else None
            raise NeedLogin(detail or f"login failed (HTTP {r.status_code})")

    def event(self, kind: str) -> None:
        """A battle whose opponent can't be read ("unreadable"), or its end ("ended")."""
        r = self.session.post(self.base + "/api/watch", timeout=10, json={"kind": kind})
        if r.status_code == 401:
            raise NeedLogin("session expired")
        r.raise_for_status()

    def status(self, state: str, detail: str | None) -> None:
        r = self.session.post(self.base + "/api/watch/status", timeout=5,
                              json={"state": state, "detail": (detail or None) and detail[:80]})
        if r.status_code == 401:
            raise NeedLogin("session expired")
        r.raise_for_status()

    def report(self, found: Sighting) -> None:
        r = self.session.post(self.base + "/api/watch", timeout=10,
                              json={"name": found.name, "clan": found.clan,
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


def text_reader() -> Callable[[Any], Any]:
    from rapidocr_onnxruntime import RapidOCR

    engine = RapidOCR()
    return lambda image: engine(image)[0]


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
    print("Top right:", [ln.text for ln in top_right])
    print("Top left: ", [ln.text for ln in top_left])
    print("In a battle:", "yes" if in_battle(top_right) else "no (start a match and check again)")
    found = read_opponent(top_left)
    if found:
        print(f"Opponent: {describe(found)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read your opponent off your mirrored phone.")
    parser.add_argument("--check", action="store_true", help="read the screen once and show it")
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
    grab = screen_grabber(load_region()) if args.manual else window_grabber(titles)
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

    watcher = Watcher(grab, text_reader(), send, event=event)
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
