import numpy as np
import pytest

from decklens.watcher import (
    TOP_LEFT, TOP_RIGHT, DeckLensClient, NeedLogin, TextLine, Watcher, crop, in_battle,
    lines_from_ocr, read_opponent,
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
    assert read_opponent(lines(REAL_TOP_LEFT, w=669, h=240)) == ("FIOReNt", "Sunny")


def test_no_clan_crown_digits_and_low_confidence_are_handled():
    name_only = [TextLine("Florent", 0.14, 0.42, 0.9)]
    assert read_opponent(name_only) == ("Florent", None)
    noisy = [TextLine("11", 0.70, 0.60, 0.95),          # tower crown level: right of the name
             TextLine("xx", 0.10, 0.10, 0.2),           # unsure read: ignored
             TextLine("Florent", 0.14, 0.42, 0.9), TextLine("Sunny", 0.15, 0.67, 0.9)]
    assert read_opponent(noisy) == ("Florent", "Sunny")
    assert read_opponent([TextLine("2580", 0.1, 0.4, 0.9)]) is None  # just a number
    assert read_opponent([]) is None


def test_ocr_junk_is_skipped():
    assert lines_from_ocr([["bad"], [None, "x", "y"], [[[1, 2]], "  ", 0.9]], 100, 100) == []
    assert lines_from_ocr(None, 100, 100) == []


def test_crop_uses_fractions_of_the_phone_screen():
    image = np.zeros((2400, 1080, 3), dtype=np.uint8)
    assert crop(image, TOP_LEFT).shape == (240, 669, 3)
    assert crop(image, TOP_RIGHT).shape[0] == 192


# --- the watch loop, with a fake screen ---------------------------------------------

BATTLE, LOADING, OTHER_BATTLE = 1, 2, 3


def frame(kind):
    return np.full((2400, 1080, 3), kind, dtype=np.uint8)


def fake_ocr(part):
    kind, top_right = int(part[0, 0, 0]), part.shape[1] < 400
    if kind == LOADING:
        return []
    if top_right:
        return REAL_TOP_RIGHT
    name = "FIOReNt" if kind == BATTLE else "Kaua"
    return [[[[93, 102], [330, 102], [330, 150], [93, 150]], name, 0.8],
            [[[99, 161], [190, 161], [190, 195], [99, 195]], "Sunny", 0.8]]


def run(kinds, **kw):
    frames = iter(frame(k) for k in kinds)
    sent = []
    watcher = Watcher(lambda: next(frames), fake_ocr, lambda n, c: sent.append((n, c)), **kw)
    for _ in kinds:
        watcher.step()
    return sent


def test_reports_once_per_battle_after_two_matching_reads():
    assert run([LOADING, BATTLE]) == []                               # one read isn't enough
    assert run([LOADING, BATTLE, BATTLE, BATTLE, BATTLE]) == [("FIOReNt", "Sunny")]


def test_rearms_after_the_battle_ends():
    kinds = [BATTLE, BATTLE, LOADING, LOADING, LOADING, OTHER_BATTLE, OTHER_BATTLE]
    assert run(kinds) == [("FIOReNt", "Sunny"), ("Kaua", "Sunny")]
    # A brief glitch (one missed timer read) doesn't count as a new battle.
    assert run([BATTLE, BATTLE, LOADING, BATTLE, BATTLE]) == [("FIOReNt", "Sunny")]


def test_failed_send_is_retried_next_look():
    frames = iter(frame(BATTLE) for _ in range(4))
    attempts = []

    def flaky(name, clan):
        attempts.append(name)
        if len(attempts) == 1:
            raise OSError("network blip")

    watcher = Watcher(lambda: next(frames), fake_ocr, flaky)
    watcher.step()
    with pytest.raises(OSError):
        watcher.step()
    assert watcher.step() == ("FIOReNt", "Sunny") and len(attempts) == 2


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
    client.report("FIOReNt", "Sunny")
    assert session.posts[1] == ("http://127.0.0.1:8000/api/watch",
                                {"name": "FIOReNt", "clan": "Sunny"})


def test_client_login_failures_and_expiry():
    client = DeckLensClient("https://decklens.example", session=FakeSession(
        FakeResponse(401, {"detail": "Wrong username or password"}), FakeResponse(401)))
    with pytest.raises(NeedLogin, match="Wrong username"):
        client.login("david", "nope")
    with pytest.raises(NeedLogin):
        client.report("x", None)


@pytest.mark.parametrize("url", ["http://decklens.example", "ftp://127.0.0.1", "javascript:x"])
def test_client_refuses_to_send_passwords_unencrypted_to_another_computer(url):
    with pytest.raises(ValueError):
        DeckLensClient(url, session=FakeSession())


# --- finding the game picture inside the mirror window ---------------------------------

from decklens.watcher import enlarge, phone_area  # noqa: E402


def game_picture(w, h, seed=0):
    """Colourful stand-in for the game, with a few near-grey rows like the arena has."""
    rng = np.random.default_rng(seed)
    img = rng.integers(60, 255, size=(h, w, 3), dtype=np.uint8)
    img[h // 2:h // 2 + 3] = 50          # thin rows close to Phone Link's grey
    return img


def window(game, *, bg=(43, 43, 43), pad=(300, 300, 32, 0), toolbar=None):
    left, right, top, bottom = pad
    h, w = game.shape[:2]
    win = np.zeros((h + top + bottom, w + left + right, 3), dtype=np.uint8)
    win[:] = bg
    if toolbar is not None:                       # a bar right across the top
        win[:toolbar[0]] = toolbar[1]
        win[5:15, 20:120] = 230                   # title text / icons
    win[top:top + h, left:left + w] = game
    return win, (top, left, h, w)


@pytest.mark.parametrize("layout", [
    dict(pad=(974, 973, 32, 1)),                                  # like the real window: wide, grey
    dict(pad=(974, 973, 32, 1), toolbar=(28, (43, 43, 43))),     # title text in the top bar
    dict(bg=(243, 243, 243), pad=(200, 200, 48, 8)),              # light theme
    dict(bg=(0, 0, 0), pad=(30, 30, 0, 0)),                       # black side bars, game full height
    dict(bg=(20, 20, 20), pad=(10, 10, 44, 10), toolbar=(40, (45, 45, 60))),  # coloured toolbar
])
def test_finds_the_game_inside_the_window(layout):
    game = game_picture(612, 1360)
    win, (top, left, h, w) = window(game, **layout)
    found = phone_area(win)
    assert found.shape[:2] == (h, w)
    assert np.array_equal(found, game)


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
    watcher = Watcher(lambda: next(frames), fake_ocr, lambda n, c: sent.append((n, c)))
    for _ in range(5):
        watcher.step()
    assert sent == [("FIOReNt", "Sunny")]


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
