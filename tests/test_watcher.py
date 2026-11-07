import numpy as np
import pytest

from decklens.watcher import (
    TOP_LEFT, TOP_RIGHT, DeckLensClient, NeedLogin, Sighting, TextLine, Watcher, crop, describe,
    in_battle, lines_from_ocr, read_opponent,
)

# What RapidOCR returned for a real battle screenshot (1080x2400), per region.
REAL_TOP_RIGHT = [[[[57, 31], [200, 31], [200, 60], [57, 60]], "Tie left:", 0.82],
                  [[[55, 57], [190, 57], [190, 110], [55, 110]], "2:54", 0.8]]
REAL_TOP_LEFT = [[[[93, 102], [330, 102], [330, 150], [93, 150]], "FIOReNt", 0.72],
                 [[[99, 161], [190, 161], [190, 195], [99, 195]], "Sunny", 0.82]]


def lines(result, w=600, h=240):
    return lines_from_ocr(result, w, h)


def test_battle_timer_means_in_battle():
    assert in_battle(lines(REAL_TOP_RIGHT))
    assert in_battle([TextLine("Overtime", 0.1, 0.1, 0.9)])
    assert not in_battle([TextLine("Battle", 0.1, 0.1, 0.9), TextLine("VS", 0.2, 0.5, 0.9)])
    assert not in_battle([])


def test_reads_name_and_clan_from_the_real_screenshot():
    assert read_opponent(lines(REAL_TOP_LEFT, w=669, h=240)) == Sighting("FIOReNt", "Sunny")


def test_no_clan_crown_digits_and_low_confidence_are_handled():
    name_only = [TextLine("Florent", 0.14, 0.42, 0.9)]
    assert read_opponent(name_only) == Sighting("Florent", None)
    noisy = [TextLine("11", 0.70, 0.60, 0.95),          # tower crown level: right of the name
             TextLine("xx", 0.10, 0.10, 0.2),           # unsure read: ignored
             TextLine("Florent", 0.14, 0.42, 0.9), TextLine("Sunny", 0.15, 0.67, 0.9)]
    assert read_opponent(noisy) == Sighting("Florent", "Sunny")
    assert read_opponent([TextLine("2580", 0.1, 0.4, 0.9)]) is None  # just a number
    # The game's own "no clan" label isn't a clan called that.
    for label in ("No Clan", "NO CLAN", "Not in a clan"):
        assert read_opponent([TextLine("Florent", 0.14, 0.42, 0.9),
                              TextLine(label, 0.15, 0.67, 0.9)]) == Sighting("Florent", None)
    assert read_opponent([]) is None


# What RapidOCR returned for a real Ranked battle (303x230 crop): the name
# "亗•ⓜⓐⓘ•亗" wasn't detected at all; the clan "§i§" came back as "STS" and
# the rating 2216 was read cleanly.
REAL_RANKED_UNREADABLE_NAME = [[[[76, 73], [120, 73], [120, 93], [76, 93]], "STS", 0.6],
                               [[[76, 102], [130, 102], [130, 119], [76, 119]], "2216", 0.79]]


def test_unreadable_name_is_never_replaced_by_the_clan():
    found = read_opponent(lines(REAL_RANKED_UNREADABLE_NAME, w=303, h=230))
    assert found == Sighting(None, "STS", 2216)       # the clan line stays the clan
    assert describe(found) == "(name unreadable) (STS), rated 2216"


def test_league_line_before_ultimate_champion_is_never_the_clan():
    # Pre-UC Ranked: name / clan / league, or name / league with no clan.
    assert read_opponent(ranked(("Dipo", 0.19), ("Persian Warrior", 0.32), ("League 4", 0.45))) == \
        Sighting("Dipo", "Persian Warrior")
    assert read_opponent(ranked(("Dipo", 0.19), ("No Clan", 0.32), ("League 4", 0.45))) == \
        Sighting("Dipo", None)
    # Clan line missed: the name is still two lines above the league.
    assert read_opponent(ranked(("Dipo", 0.19), ("LEAGUE10", 0.45))) == Sighting("Dipo", None)
    # The real case: name "javitop" missed, clan "Nova Reborn" read. The league
    # line shows it's the clan, so it's never searched as a name.
    assert read_opponent(ranked(("Nova Reborn", 0.32), ("League 4", 0.45))) == \
        Sighting(None, "Nova Reborn")
    # A clan that merely starts with "League" is still a clan.
    assert read_opponent(ranked(("Dipo", 0.19), ("League of Legends", 0.32))).clan == "League of Legends"


def test_ipad_battle_corner_from_a_real_screenshot():
    """Positions RapidOCR gave on a real iPad battle (5KPlayer, 20-win challenge):
    the king's health is mid-screen and tower health sits just below the corner."""
    tl = [TextLine("MONO", 0.11, 0.08, 0.79, 0.25), TextLine("4725", 0.78, 0.08, 0.72, 0.18),
          TextLine("taller telo", 0.11, 0.40, 0.87, 0.16),
          TextLine("1733", 0.45, 0.95, 0.75, 0.05)]       # tower health, clipped at the edge
    assert read_opponent(tl) == Sighting("MONO", "taller telo")     # not "rated 1733"
    # In Ranked the rating line comes next, directly under the clan.
    ranked_tl = tl[:3] + [TextLine("2216", 0.11, 0.70, 0.8, 0.16)] + tl[3:]
    assert read_opponent(ranked_tl) == Sighting("MONO", "taller telo", 2216)


def test_ipad_ranked_league_line_in_a_tall_box():
    """Real iPad Ranked battle (League 3): RapidOCR read "celvas" fine, but the
    league line's box is taller than plain digits, so measured in its height the
    name looked like it sat in the clan slot and was dropped."""
    tl = [TextLine("celvas", 0.11, 0.10, 0.79, 0.29),
          TextLine("LOSPATRONES", 0.12, 0.45, 0.85, 0.19),
          TextLine("League 3", 0.11, 0.72, 0.84, 0.26)]
    assert read_opponent(tl) == Sighting("celvas", "LOSPATRONES")
    assert read_opponent([tl[0], tl[2]]) == Sighting("celvas", None)   # clan missed
    assert read_opponent(tl[1:]) == Sighting(None, "LOSPATRONES")      # name missed


def ranked(*rows):
    """Lines laid out like the Ranked battle screen: text height 0.08, pitch 0.13."""
    return [TextLine(text, 0.25, y, 0.9, 0.08) for text, y in rows]


def test_ranked_layout_name_clan_rating():
    assert read_opponent(ranked(("Dipo", 0.19), ("Persian Warrior", 0.32), ("2216", 0.45))) == \
        Sighting("Dipo", "Persian Warrior", 2216)
    # Clan unreadable, name readable: the name keeps its place (two lines up).
    assert read_opponent(ranked(("Dipo", 0.19), ("2216", 0.45))) == Sighting("Dipo", None, 2216)
    # Only a rating: nothing to search by, so nothing is reported yet.
    assert read_opponent(ranked(("2216", 0.45))) is None
    # Spaces OCR puts in numbers are fine; crown levels and junk numbers aren't ratings.
    assert read_opponent(ranked(("Dipo", 0.19), ("Clan", 0.32), ("2 216", 0.45))).rating == 2216
    assert read_opponent(ranked(("Dipo", 0.19), ("Clan", 0.32), ("16", 0.45))).rating is None


def test_ocr_junk_is_skipped():
    assert lines_from_ocr([["bad"], [None, "x", "y"], [[[1, 2]], "  ", 0.9]], 100, 100) == []
    assert lines_from_ocr(None, 100, 100) == []


def test_crop_uses_fractions_of_the_phone_screen():
    image = np.zeros((2400, 1080, 3), dtype=np.uint8)
    assert crop(image, TOP_LEFT).shape == (240, 669, 3)
    assert crop(image, TOP_RIGHT).shape[0] == 192


# --- the watch loop, with a fake screen ---------------------------------------------

BATTLE, LOADING, OTHER_BATTLE, ARABIC = 1, 2, 3, 4


def frame(kind):
    return np.full((2400, 1080, 3), kind, dtype=np.uint8)


def fake_ocr(part):
    kind, top_right = int(part[0, 0, 0]), part.shape[1] < 400
    if kind == LOADING:
        return []
    if top_right:
        return REAL_TOP_RIGHT
    if kind == ARABIC:  # what RapidOCR made of an Arabic name and clan on a real iPad
        return [[[[93, 30], [130, 30], [130, 60], [93, 60]], "92", 0.52],
                [[[93, 160], [130, 160], [130, 190], [93, 190]], "30", 0.65]]
    name = "FIOReNt" if kind == BATTLE else "Kaua"
    return [[[[93, 102], [330, 102], [330, 150], [93, 150]], name, 0.8],
            [[[99, 161], [190, 161], [190, 195], [99, 195]], "Sunny", 0.8]]


def run(kinds, **kw):
    frames = iter(frame(k) for k in kinds)
    sent = []
    watcher = Watcher(lambda: next(frames), fake_ocr, sent.append, **kw)
    for _ in kinds:
        watcher.step()
    return sent


def test_reports_once_per_battle_after_two_matching_reads():
    assert run([LOADING, BATTLE]) == []                               # one read isn't enough
    assert run([LOADING, BATTLE, BATTLE, BATTLE, BATTLE]) == [Sighting("FIOReNt", "Sunny")]


def test_rearms_after_the_battle_ends():
    kinds = [BATTLE, BATTLE, LOADING, LOADING, LOADING, OTHER_BATTLE, OTHER_BATTLE]
    assert run(kinds) == [Sighting("FIOReNt", "Sunny"), Sighting("Kaua", "Sunny")]
    # A brief glitch (one missed timer read) doesn't count as a new battle.
    assert run([BATTLE, BATTLE, LOADING, BATTLE, BATTLE]) == [Sighting("FIOReNt", "Sunny")]


def run_events(kinds, **kw):
    frames = iter(frame(k) for k in kinds)
    sent, events = [], []
    watcher = Watcher(lambda: next(frames), fake_ocr, sent.append, event=events.append, **kw)
    for _ in kinds:
        watcher.step()
    return sent, events


def test_unreadable_battle_is_flagged_once_and_its_end_reported():
    kinds = [ARABIC] * 8 + [LOADING] * 3
    sent, events = run_events(kinds)
    assert sent == [] and events == ["unreadable", "ended"]
    # Readable battles don't trigger either note.
    sent, events = run_events([BATTLE] * 8 + [LOADING] * 3)
    assert sent == [Sighting("FIOReNt", "Sunny")] and events == []
    # A few unreadable looks (loading in) followed by a readable name: no note.
    sent, events = run_events([ARABIC] * 3 + [BATTLE] * 3 + [LOADING] * 3)
    assert len(sent) == 1 and events == []


def test_a_failed_note_never_stops_the_watcher():
    frames = iter(frame(k) for k in [ARABIC] * 6 + [LOADING] * 3 + [BATTLE] * 2)

    def broken(kind):
        raise OSError("network blip")

    sent = []
    watcher = Watcher(lambda: next(frames), fake_ocr, sent.append, event=broken)
    for _ in range(11):
        watcher.step()
    assert sent == [Sighting("FIOReNt", "Sunny")]      # the next battle still works


def test_client_sends_battle_notes_as_text_only():
    session = FakeSession(FakeResponse(200, {"id": 3}))
    client = DeckLensClient("http://127.0.0.1:8000", session=session)
    client.event("unreadable")
    assert session.posts == [("http://127.0.0.1:8000/api/watch", {"kind": "unreadable"})]


def test_failed_send_is_retried_next_look():
    frames = iter(frame(BATTLE) for _ in range(4))
    attempts = []

    def flaky(found):
        attempts.append(found)
        if len(attempts) == 1:
            raise OSError("network blip")

    watcher = Watcher(lambda: next(frames), fake_ocr, flaky)
    watcher.step()
    with pytest.raises(OSError):
        watcher.step()
    assert watcher.step() == Sighting("FIOReNt", "Sunny") and len(attempts) == 2


def test_read_again_reports_the_same_battle_once_more():
    asks = iter([False, False, False, True, False, False, False])
    sent = run([BATTLE] * 7, rescan=lambda: next(asks))
    assert sent == [Sighting("FIOReNt", "Sunny")] * 2    # read again on request, once


# --- talking to DeckLens --------------------------------------------------------------


class FakeResponse:
    def __init__(self, status, body=None):
        self.status_code, self._body = status, body or {}
        self.headers = {"content-type": "application/json"}

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise OSError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, *responses):
        self.headers, self.responses, self.posts = {}, list(responses), []

    def post(self, url, **kw):
        self.posts.append((url, kw["json"]))
        return self.responses.pop(0)


def test_client_logs_in_and_reports_text_only_from_its_own_origin():
    session = FakeSession(FakeResponse(200), FakeResponse(200, {"id": 1}))
    client = DeckLensClient("http://127.0.0.1:8000/anything", session=session)
    assert session.headers["Origin"] == "http://127.0.0.1:8000"
    client.login("david", "correct horse battery staple")
    client.report(Sighting("FIOReNt", "Sunny", 2216))
    assert session.posts[1] == ("http://127.0.0.1:8000/api/watch",
                                {"name": "FIOReNt", "clan": "Sunny", "rating": 2216})


def test_client_login_failures_and_expiry():
    client = DeckLensClient("https://decklens.example", session=FakeSession(
        FakeResponse(401, {"detail": "Wrong username or password"}), FakeResponse(401)))
    with pytest.raises(NeedLogin, match="Wrong username"):
        client.login("david", "nope")
    with pytest.raises(NeedLogin):
        client.report(Sighting("x"))


@pytest.mark.parametrize("url", [
    "http://decklens.example", "ftp://127.0.0.1", "javascript:x",
    "http://evil.example\\@127.0.0.1:8000",      # urlsplit sees 127.0.0.1, requests sees evil
    "http://user:pw@127.0.0.1:8000", "http://evil.example@localhost",
    "http://127.0.0.1:99999", "http://127.0.0.1:8000\tx", "https://", "http://127.0.0.1.evil.example",
])
def test_client_refuses_to_send_passwords_unencrypted_to_another_computer(url):
    with pytest.raises(ValueError):
        DeckLensClient(url, session=FakeSession())


@pytest.mark.parametrize("url, base", [
    ("http://LOCALHOST:8000/anything", "http://localhost:8000"),
    ("http://[::1]:8000", "http://[::1]:8000"),
    ("https://DeckLens.example/", "https://decklens.example"),
])
def test_client_server_address_is_rebuilt_from_its_parts(url, base):
    assert DeckLensClient(url, session=FakeSession()).base == base


def test_client_never_follows_redirects():
    """requests re-sends the body on a 307/308, so a redirect could carry the password away."""
    sent = []

    class Recorder(FakeSession):
        def post(self, url, **kw):
            sent.append(kw)
            return super().post(url, **kw)

    client = DeckLensClient("http://127.0.0.1:8000", session=Recorder(
        FakeResponse(200), FakeResponse(200, {"id": 1}), FakeResponse(200, {}), FakeResponse(200)))
    client.login("david", "correct horse battery staple")
    client.report(Sighting("x"))
    client.status("connected", None)
    client.event("ended")
    assert len(sent) == 4 and all(kw["allow_redirects"] is False for kw in sent)


# --- finding the game picture inside the mirror window ---------------------------------

from decklens.watcher import enlarge, phone_area  # noqa: E402


def game_picture(w, h, seed=0):
    """Colourful stand-in for the game, with a few near-grey rows like the arena has."""
    rng = np.random.default_rng(seed)
    img = rng.integers(60, 255, size=(h, w, 3), dtype=np.uint8)
    img[h // 2:h // 2 + 3] = 50          # thin rows close to Phone Link's grey
    return img


def window(game, *, bg=(43, 43, 43), pad=(300, 300, 32, 0), toolbar=None, border=None):
    left, right, top, bottom = pad
    h, w = game.shape[:2]
    win = np.zeros((h + top + bottom, w + left + right, 3), dtype=np.uint8)
    win[:] = bg
    if toolbar is not None:                       # a bar right across the top
        win[:toolbar[0]] = toolbar[1]
        win[5:15, 20:120] = 230                   # title text / icons
    if border is not None:                        # 1-px line some apps draw along the top
        win[0] = border
    win[top:top + h, left:left + w] = game
    return win, (top, left, h, w)


@pytest.mark.parametrize("layout", [
    dict(pad=(974, 973, 32, 1)),                                  # like the real window: wide, grey
    dict(pad=(974, 973, 32, 1), toolbar=(28, (43, 43, 43))),     # title text in the top bar
    dict(bg=(243, 243, 243), pad=(200, 200, 48, 8)),              # light theme
    dict(bg=(0, 0, 0), pad=(30, 30, 0, 0)),                       # black side bars, game full height
    dict(bg=(20, 20, 20), pad=(10, 10, 44, 10), toolbar=(40, (45, 45, 60))),  # coloured toolbar
    # Phone Link's "Phone screen" window (Nov 2026): narrow, the game fills its width and
    # reaches the bottom; only the title bar ("<- Phone screen ... - [] x") is plain.
    dict(pad=(0, 0, 45, 0), toolbar=(45, (43, 43, 43))),
    dict(pad=(0, 0, 45, 0), toolbar=(45, (43, 43, 43)), border=(110, 110, 110)),
    dict(pad=(0, 0, 45, 12), toolbar=(45, (43, 43, 43)), border=(110, 110, 110)),  # + grey foot
])
def test_finds_the_game_inside_the_window(layout):
    game = game_picture(612, 1360)
    win, (top, left, h, w) = window(game, **layout)
    found = phone_area(win)
    assert found.shape[:2] == (h, w)
    assert np.array_equal(found, game)


def test_title_bar_is_cut_when_the_game_fades_to_black_at_the_bottom():
    """Two plain edges in different colours (grey title bar, black game foot) are both
    background. Averaging them made the title bar count as game, and the watcher read
    "Phone screen" as the opponent's name (real Phone Link window, 2026-11-07)."""
    game = game_picture(612, 1360)
    game[-20:] = (1, 0, 1)                          # the arena's dark bottom edge
    win, (top, left, h, w) = window(game, pad=(0, 0, 45, 0), toolbar=(45, (43, 43, 43)),
                                    border=(110, 110, 110))
    found = phone_area(win)
    assert found.shape[1] == w and h - 20 <= found.shape[0] <= h
    assert np.array_equal(found[:100], game[:100])  # starts at the game, not the title bar


def test_finds_an_ipad_game_inside_an_airplay_window():
    # 5KPlayer on a real PC: 4:3 game picture centred in a wide black window.
    game = game_picture(1020, 1347)
    win, _ = window(game, bg=(0, 0, 0), pad=(770, 770, 0, 0))
    assert np.array_equal(phone_area(win), game)


def test_game_filling_the_window_is_used_whole():
    game = game_picture(540, 1200)
    assert phone_area(game).shape == game.shape


def test_odd_shapes_fall_back_to_the_whole_window():
    wide = np.full((400, 1600, 3), 43, dtype=np.uint8)
    wide[50:350, 100:1500] = game_picture(1400, 300)   # landscape: not a phone
    assert phone_area(wide).shape == wide.shape
    assert phone_area(np.zeros((20, 20, 3), dtype=np.uint8)).shape == (20, 20, 3)


def test_enlarge_leaves_big_regions_alone():
    big = np.zeros((100, 600, 3), dtype=np.uint8)
    assert enlarge(big) is big


def test_window_missing_counts_as_not_in_battle():
    frames = iter([None, None, None, frame(BATTLE), frame(BATTLE)])
    sent = []
    watcher = Watcher(lambda: next(frames), fake_ocr, sent.append)
    for _ in range(5):
        watcher.step()
    assert sent == [Sighting("FIOReNt", "Sunny")]


# --- choosing the mirror window -----------------------------------------------------

from decklens.wincapture import Window, choose_window  # noqa: E402

DESKTOP = [
    Window(1, "DeckLens - Brave", "brave.exe"),
    Window(2, "Phone Link guide | Clash Royale - Brave", "brave.exe"),  # mentions it: never picked
    Window(3, "Phone Link", "phoneexperiencehost.exe"),               # Phone Link's main window
    Window(4, "David's A53", "yourphoneappproxyhost.exe"),            # the mirror (real PC listing)
    Window(5, "Windows PowerShell", "windowsterminal.exe"),
]


def test_mirror_window_found_through_phone_link_whatever_the_phone_is_called():
    assert choose_window(DESKTOP, []).handle == 4
    renamed = [w if w.handle != 4 else Window(4, "Pixel 9 Pro", w.process) for w in DESKTOP]
    assert choose_window(renamed, []).handle == 4


def test_only_phone_links_main_window_means_no_mirror_yet():
    assert choose_window([w for w in DESKTOP if w.handle != 4], []) is None


def test_a_named_window_wins_and_must_match_exactly():
    assert choose_window(DESKTOP, ["david's a53"]).handle == 4
    assert choose_window(DESKTOP, ["windows powershell"]).handle == 5
    # Not an exact title: falls back to finding Phone Link, never a browser tab.
    assert choose_window(DESKTOP, ["Phone Link guide"]).handle == 4


def test_real_window_list_from_a_pc():
    """What --list-windows showed on a real PC: the mirror is the only Phone Link window."""
    real = [Window(1, "Windows PowerShell", "windowsterminal.exe"), Window(2, "Arc", "arc.exe"),
            Window(3, "DeckLens - Brave", "brave.exe"),
            Window(4, "David's A53", "yourphoneappproxyhost.exe"),
            Window(5, "Settings", "applicationframehost.exe"), Window(6, "XBOX", "xboxpcapp.exe"),
            Window(7, "Program Manager", "explorer.exe")]
    assert choose_window(real, []).title == "David's A53"


def test_airplay_mirror_window_found_by_program_for_an_ipad():
    """--list-windows with 5KPlayer mirroring an iPad: the title has a counter that changes."""
    real = [Window(1, "Windows PowerShell", "windowsterminal.exe"), Window(2, "Arc", "arc.exe"),
            Window(3, "iPad (2)", "airplay.exe"), Window(4, "Overwolf", "overwolf.exe"),
            Window(5, "Program Manager", "explorer.exe")]
    assert choose_window(real, []).title == "iPad (2)"
    renamed = [w if w.handle != 3 else Window(3, "iPad", "airplay.exe") for w in real]
    assert choose_window(renamed, ["iPad (2)"]).handle == 3       # old remembered title: still found
    # 5KPlayer's own title bar and control bar are windows too: never picked.
    with_helpers = [Window(1, "TitleForm", "airplay.exe", 812 * 22),
                    Window(2, "ControlForm", "airplay.exe", 300 * 70),
                    Window(3, "iPad (2)", "airplay.exe", 1080 * 1439),
                    Window(4, "Arc", "arc.exe", 2000 * 1100)]
    assert choose_window(with_helpers, []).title == "iPad (2)"
    # Other unknown helper windows lose to the biggest one (the picture).
    other = [Window(1, "Overlay", "airplay.exe", 400 * 30), Window(3, "iPad", "airplay.exe", 900 * 1200)]
    assert choose_window(other, []).title == "iPad"
    # Phone Link's mirror is preferred when both are open.
    both = real + [Window(6, "David's A53", "yourphoneappproxyhost.exe")]
    assert choose_window(both, []).title == "David's A53"



# --- connection status ---------------------------------------------------------------

from decklens.watcher import Heartbeat, window_grabber  # noqa: E402


def test_heartbeat_sends_changes_at_once_and_repeats_every_few_seconds():
    clock, sent = [0.0], []
    beat = Heartbeat(lambda s, d: sent.append((s, d)), every=5, clock=lambda: clock[0])
    beat("starting")
    beat("connected", "David's A53")
    beat("connected", "David's A53")                 # same, too soon: skipped
    clock[0] = 6
    beat("connected", "David's A53")                 # still alive: repeated
    assert sent == [("starting", None), ("connected", "David's A53"),
                    ("connected", "David's A53")]


def test_heartbeat_survives_network_trouble():
    calls = []

    def flaky(state, detail):
        calls.append(state)
        if len(calls) == 1:
            raise OSError("server restarting")

    beat = Heartbeat(flaky, every=5, clock=lambda: 0.0)
    beat("connected", "x")                           # fails quietly...
    beat("connected", "x")                           # ...and is tried again next look
    assert calls == ["connected", "connected"]


def test_window_grabber_reports_what_it_can_see(monkeypatch):
    import sys
    import types

    from decklens import wincapture

    mirror = Window(4, "David's A53", "yourphoneappproxyhost.exe")
    picture = np.full((1400, 2560, 3), 43, dtype=np.uint8)
    picture[30:1390, 974:1586] = game_picture(612, 1360)
    world = {"window": None, "image": None, "box": None}
    monkeypatch.setattr(wincapture, "find_window", lambda titles: world["window"])
    monkeypatch.setattr(wincapture, "capture", lambda w: world["image"])
    monkeypatch.setattr(wincapture, "client_box", lambda w: world["box"])

    class FakeScreen:
        def grab(self, box):
            return np.dstack([picture[:, :, ::-1], np.full(picture.shape[:2], 255, np.uint8)])

    monkeypatch.setitem(sys.modules, "mss", types.SimpleNamespace(mss=FakeScreen))
    seen = []
    grab = window_grabber([], on_status=lambda s, d: seen.append((s, d)))

    assert grab() is None and seen[-1] == ("no_window", None)          # Phone Link not open
    world.update(window=mirror, image=picture)
    assert grab().shape[:2] == (1360, 612) and seen[-1] == ("connected", "David's A53")
    world.update(image=None, box=None)                                 # minimised
    assert grab() is None and seen[-1] == ("minimised", "David's A53")
    world.update(box=(0, 0, 2560, 1400))                               # visible, but no window picture
    assert grab().shape[:2] == (1360, 612) and seen[-1] == ("screen_only", "David's A53")


def test_heartbeat_passes_on_the_pages_read_again_request():
    answers = iter([False, True])
    beat = Heartbeat(lambda s, d: next(answers), every=0, clock=lambda: 0.0)
    beat("connected", "x")
    assert beat.take_rescan() is False
    beat("connected", "x")
    assert beat.take_rescan() is True and beat.take_rescan() is False   # once per ask


def test_client_status_returns_the_read_again_flag():
    session = FakeSession(FakeResponse(200, {"ok": True, "rescan": True}),
                          FakeResponse(200, {"ok": True, "rescan": False}))
    client = DeckLensClient("http://127.0.0.1:8000", session=session)
    assert client.status("connected", "iPad") is True
    assert client.status("connected", "iPad") is False
