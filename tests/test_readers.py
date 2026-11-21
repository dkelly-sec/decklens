"""The text readers: choosing between readings, Arabic order, and fetching models."""

import hashlib

import numpy as np
import pytest

from decklens import readers
from decklens.readers import (
    SCRIPTS, BadDownload, TextReader, ensure_model, fetch, has_han_or_kana, pick, reread,
    visual_to_logical,
)
from decklens.watcher import lines_from_ocr

BY_NAME = {s.name: s for s in SCRIPTS}
ARABIC, KOREAN, CYRILLIC, THAI, GREEK = (BY_NAME[n] for n in ("arabic", "korean", "cyrillic", "th", "el"))


# --- choosing a reading --------------------------------------------------------------

def test_a_script_reader_beats_confident_nonsense():
    # Real reads from rendered corners: every Latin reader turns these into Latin.
    assert pick(("AnMa", 0.9), ("", 0.0), {CYRILLIC: ("Дима", 0.73)})[0] == "Дима"
    assert pick(("Lesi", 0.95), ("", 0.0), {ARABIC: ("أحمد", 0.8)})[0] == "أحمد"
    assert pick(("inw", 0.86), ("lnw", 0.75), {THAI: ("เทพ", 0.77)})[0] == "เทพ"


def test_a_script_reader_beats_chinese_only_when_surer():
    # Hangul comes back from the multilingual reader as Chinese (김민수 -> 召).
    assert pick(("", 0.0), ("召", 0.98), {KOREAN: ("김민수", 1.0)})[0] == "김민수"
    assert pick(("", 0.0), ("猫のお茶会", 0.99), {KOREAN: ("고양이", 0.7)})[0] == "猫のお茶会"


def test_latin_names_stay_latin():
    seen = {CYRILLIC: ("Florent", 1.0), GREEK: ("Florent", 1.0), THAI: ("Phนc", 0.9)}
    assert pick(("Florent", 0.99), ("Florent", 1.0), seen)[0] == "Florent"
    # Hangul ㅇ and ㅣ look like O and I: a sure Latin read wins.
    assert pick(("oJ", 0.61), ("oj", 0.9), {KOREAN: ("이", 0.98)})[0] == "oJ"
    assert pick(("OJ", 0.9), ("", 0.0), {KOREAN: ("이이", 0.98)})[0] == "OJ"


def test_cyrillic_and_greek_need_a_letter_that_does_not_look_latin():
    assert not CYRILLIC.owns("РОСКЕТ")       # all look-alikes: could be ROCKET
    assert CYRILLIC.owns("Саша") and CYRILLIC.owns("Братва")
    assert not GREEK.owns("ΚΟΤΑ") and GREEK.owns("Γιώργος")
    assert pick(("ROCKET", 0.9), ("", 0.0), {CYRILLIC: ("РОСКЕТ", 0.99)})[0] == "ROCKET"


def test_a_script_must_be_most_of_the_reading_and_two_letters():
    assert not THAI.owns("Phนc") and not THAI.owns("ป") and THAI.owns("ไทยแลนด์")
    assert ARABIC.owns("محمد CR") and not ARABIC.owns("Mohamed م")


def test_unsure_script_readings_are_ignored():
    assert pick(("AnMa", 0.9), ("", 0.0), {CYRILLIC: ("Дима", 0.5)})[0] == "AnMa"


def test_han_and_kana_are_told_from_other_scripts():
    assert has_han_or_kana("四暗刻単騎") and has_han_or_kana("ミナ") and has_han_or_kana("ｹﾝ")
    assert not has_han_or_kana("Florent") and not has_han_or_kana("철수") and not has_han_or_kana("2:54")


# --- Arabic order ----------------------------------------------------------------------

def test_arabic_reads_come_back_in_reading_order():
    assert visual_to_logical("ىلعوبا") == "ابوعلى"
    assert visual_to_logical("10 دمحم CR") == "CR محمد 10"


def test_arabic_pieces_on_one_row_join_right_to_left():
    def box(left, right):
        return [[left, 10], [right, 10], [right, 40], [left, 40]]
    lines = lines_from_ocr([[box(10, 90), "الخليج", 0.9], [box(100, 160), "نجوم", 0.9]], 600, 200)
    assert [ln.text for ln in lines] == ["نجوم الخليج"]
    lines = lines_from_ocr([[box(10, 90), "Team", 0.9], [box(100, 160), "Queso", 0.9]], 600, 200)
    assert [ln.text for ln in lines] == ["Team Queso"]


# --- reading every piece ---------------------------------------------------------------

def _picture():
    return np.zeros((200, 600, 3), dtype=np.uint8)


def test_every_piece_but_the_rating_is_read_by_each_script():
    asked = []

    def arabic(part):
        asked.append("arabic")
        return "محمد", 0.95

    main = [[[[20, 20], [200, 20], [200, 60], [20, 60]], "dJgeuwl", 0.58],
            [[[20, 130], [120, 130], [120, 160], [20, 160]], "2106", 0.99]]
    out = reread(_picture(), main, lambda part: ("", 0.0), {ARABIC: arabic})
    assert [t for _, t, _ in out] == ["محمد", "2106"]
    assert asked == ["arabic"]


def test_a_sure_cyrillic_name_vouches_for_a_latin_looking_clan():
    def box(top):
        return [[20, top], [200, top], [200, top + 30], [20, top + 30]]
    readings = {"Cepreu": ("Сергей", 0.9), "MocKBa": ("Москва", 0.95)}

    def read(*pieces):
        order = list(pieces)
        main = [[box(10 + 40 * i), text, 0.9] for i, text in enumerate(pieces)]
        out = reread(_picture(), main, None, {CYRILLIC: lambda part: readings[order.pop(0)]})
        return [t for _, t, _ in out]

    assert read("Cepreu", "MocKBa") == ["Сергей", "Москва"]
    assert read("MocKBa") == ["MocKBa"]       # alone, Москва could be MOCKBA in Latin


def test_the_timer_corner_uses_the_main_reader_only():
    calls = []

    def main(image, text_score=0.5):
        calls.append(text_score)
        return [[[[0, 0], [9, 0], [9, 9], [0, 9]], "Time left:", 0.9]], None

    reader = TextReader(main, lambda part: ("never", 1.0), {ARABIC: lambda part: ("never", 1.0)})
    assert reader(_picture())[0][1] == "Time left:"
    assert calls == [0.5]
    assert reader.names(_picture())[0][1] == "Time left:"
    assert calls == [0.5, 0.0]                # names: every piece, sure or not
    assert "Arabic" in reader.describe()


# --- fetching models -------------------------------------------------------------------

class _Response:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        pass

    def iter_content(self, size):
        for i in range(0, len(self.body), size):
            yield self.body[i:i + size]


def _sha(body):
    return hashlib.sha256(body).hexdigest()


def test_fetch_keeps_only_the_expected_file(tmp_path):
    good = b"model bytes"
    dest = tmp_path / "m.onnx"
    fetch("https://example.test/m", _sha(good), 100, dest, lambda *a, **k: _Response(good))
    assert dest.read_bytes() == good
    with pytest.raises(BadDownload):
        fetch("https://example.test/m", _sha(good), 100, tmp_path / "x.onnx",
              lambda *a, **k: _Response(b"something else"))
    with pytest.raises(BadDownload):
        fetch("https://example.test/m", _sha(good), 5, tmp_path / "y.onnx",
              lambda *a, **k: _Response(good))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["m.onnx"]    # nothing half-written


def test_models_are_checked_before_every_use(tmp_path, monkeypatch):
    model, keys = b"weights", b"\nA\nB\n"
    script = readers.Script("test", "Test", ("LATIN",), _sha(model), 100, _sha(keys))
    urls = []

    def get(url, **kw):
        urls.append(url)
        return _Response(model if url.endswith(".onnx") else keys)

    found = ensure_model(script, tmp_path, get=get)
    assert found is not None and len(urls) == 2
    assert all(readers.MODELS_COMMIT in u for u in urls)
    assert ensure_model(script, tmp_path, get=get) == found and len(urls) == 2   # no refetch
    found[0].write_bytes(b"tampered")
    assert ensure_model(script, tmp_path, download=False) is None
    ensure_model(script, tmp_path, get=get)                                    # fetched again
    assert found[0].read_bytes() == model and len(urls) == 3


def test_the_pinned_models_are_https_and_hashed():
    for script in SCRIPTS:
        assert len(script.model_sha256) == len(script.keys_sha256) == 64
        assert 1_000_000 < script.model_bytes < 20_000_000
    assert readers._MODEL_URL.startswith("https://") and readers._KEYS_URL.startswith("https://")
