"""Reading text off the battle screen, in the scripts players' names are written in.

All readers are offline ONNX models run by RapidOCR; pictures never leave the
computer. Three kinds:

- The main reader (PP-OCRv4, bundled with rapidocr_onnxruntime) finds the pieces of
  text and reads Latin letters, digits and Chinese. It alone reads the timer corner.
- The multilingual reader (PP-OCRv6, bundled with rapidocr) reads Chinese in both
  forms and Japanese. The main one writes kanji in simplified Chinese (単騎 -> 单骑)
  and can't read kana at all.
- One reader per script (PaddleOCR's PP-OCRv5 models): Arabic (with Persian and
  Urdu letters), Korean, Cyrillic, Thai, Greek and Devanagari. The other readers
  turn these into confident nonsense: Дима -> "AnMa", ابو علي -> "Lesi", 철수 -> 彗.
  No package bundles them, so they are fetched once into ~/.decklens/models from a
  pinned commit of a public model repository and checked against pinned SHA-256
  hashes, on download and before every use: the files read are exactly the ones
  measured.

In the name corner, each piece of text the main reader finds is read by all of them
and pick() chooses. A script reader wins when it reads mostly its own script, at
least two letters, surely; Cyrillic and Greek also need a letter that doesn't look
Latin, since every reader can read "ROCKET" as РОСКЕТ. Hangul loses to a sure Latin
reading: ㅇ and ㅣ look like O and I, so "OJ" can come back as 이.

Measured on 300 rendered battle corners with stand-in fonts (names read to the
right search key, before -> now): Arabic 0 -> 38 of 40, Russian 10 -> 32 of 32,
Korean 0 -> 16 of 16, Thai 0 -> 7 of 8, Greek 0 -> 4 of 4, Hindi 0 -> 4 of 4; Latin,
Chinese and Japanese unchanged. About 0.4 s more per look while the name is being
read (the first few seconds of a battle); the timer corner gets faster.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("decklens.watcher")

MIN_CONFIDENCE = 0.5     # under this, a piece of text is ignored
SCRIPT_CONFIDENCE = 0.6  # a script reader must be this sure to be taken
REREAD_PAD = 0.15        # around each piece of text, in its heights

Read = tuple[str, float]                 # (text, confidence)
Recognise = Callable[[Any], Read]        # one piece of text -> its reading


def has_han_or_kana(text: str) -> bool:
    """Chinese characters (also used in Japanese) or Japanese kana in text."""
    return any(unicodedata.name(ch, "").startswith(
        ("CJK ", "HIRAGANA ", "KATAKANA ", "HALFWIDTH KATAKANA ")) for ch in text)


def _letters(text: str) -> list[str]:
    return [ch for ch in text if unicodedata.category(ch).startswith("L")]


def _plain_latin(text: str) -> bool:
    return bool(text.strip()) and text.isascii() and any(ch.isalpha() for ch in text)


def _is_rating(text: str) -> bool:
    digits = text.replace(" ", "")
    return digits.isdigit() and len(digits) >= 3


# Cyrillic and Greek letters that look like Latin ones. A reading made only of
# these says nothing about the script: it is what "ROCKET" looks like too.
LOOKS_LATIN = frozenset("АВЕЅІЈКМНОРСТУХҮаеѕіјорсухүԁԛԝвкмнт" "ΑΒΕΖΗΙΚΜΝΟΡΤΥΧοικνρτυχαϲ")
_LTR_RUN = re.compile(r"[0-9A-Za-z](?:[0-9A-Za-z .:_\-]*[0-9A-Za-z])?")


def visual_to_logical(text: str) -> str:
    """The Arabic reader goes left to right, so its text comes out in screen order:
    the last letter first. Reverse it, keeping runs of digits and Latin letters in
    their own order ("CR محمد 10" is read as "10 دمحم CR")."""
    return _LTR_RUN.sub(lambda m: m.group(0)[::-1], text[::-1])


@dataclass(frozen=True)
class Script:
    name: str                 # PaddleOCR's name for the model's language
    label: str                # for people
    letters: tuple[str, ...]  # starts of the Unicode names of its letters
    model_sha256: str
    model_bytes: int
    keys_sha256: str
    looks_latin: bool = False
    rtl: bool = False

    def owns(self, text: str, *, trusted: bool = False) -> bool:
        """text is mostly this script's letters (at least two letters in all).
        trusted: the script was already seen in this corner, so letters that look
        Latin are enough (a Russian name's clan can be Москва)."""
        letters = _letters(text)
        mine = [ch for ch in letters if unicodedata.name(ch, "").startswith(self.letters)]
        if len(letters) < 2 or 2 * len(mine) < len(letters):
            return False
        return trusted or not self.looks_latin or any(ch not in LOOKS_LATIN for ch in mine)


SCRIPTS = (
    Script("arabic", "Arabic", ("ARABIC",), "b58f7d1cc7200566aae1d56ac26c17942881958ed100ac5a07d9a6e67150cbef",
           8023442, "f6b5baf1335408e6e1fb5cc6e8268a26769f549c2bb37268e72a6804fe269b4c", rtl=True),
    Script("korean", "Korean", ("HANGUL",), "ee0dfde503d787c91fd0455daae2eb85311b4b5bdcddf85a54d8c1e0adc157de",
           13443278, "a3792cbb41215a43e555e16ff4a7f7b18db5dd80cd058637098f0d872f2dd9d6"),
    Script("cyrillic", "Cyrillic", ("CYRILLIC",), "0d91040605ef7bdca6868fad6cad1664840d031e9f6d40d54607df5b50fa280a",
           8073294, "90822a96058eb83f8e637ace75f90cbd36c41253f9dbdfb88fcf5a2bdd50d669", looks_latin=True),
    Script("th", "Thai", ("THAI",), "6f07aa2af58713da04b4bdaf1524588d8051fc734db5a1ad7b7a65c1e7c7ec18",
           7915510, "eaacae5cf9b0808d9078ce1152c9ea5c6099d1f563750e66b93c12854acea31a"),
    Script("el", "Greek", ("GREEK",), "54235f471b1ca8e356c48673125e0b14e4823496cd7bb583b24fb8808dbcd9c4",
           7833230, "17147b8921fbcc7d2bcbc69cb7dbb7ae61cc0808e6dec1b311d746ffe5f6afed", looks_latin=True),
    Script("devanagari", "Devanagari", ("DEVANAGARI",), "932e2d53fafdfd929131c4e20133f49901288e8ec1ba03b18317a4780b21f3ce",
           7936806, "4bfb5fc511c24da3b1f3a582ae4e07ecd16601997b70b864bb34e111fc646cfe"),
)


def pick(main: Read, multi: Read | None, by_script: dict[Script, Read],
         trusted: frozenset[Script] = frozenset()) -> Read:
    """Choose one reading of a piece of text (see the module docstring). trusted:
    scripts already read for sure elsewhere in the corner."""
    text, sure = main
    if multi is not None:
        again, sure_again = multi
        if again.strip() and sure_again >= MIN_CONFIDENCE and (
                has_han_or_kana(text) or sure < MIN_CONFIDENCE or has_han_or_kana(again)):
            text, sure = again.strip(), sure_again
    latin = max((s for t, s in (main, multi or ("", 0.0)) if _plain_latin(t)), default=0.0)
    best: Read | None = None
    for script, (seen, how_sure) in by_script.items():
        if how_sure < SCRIPT_CONFIDENCE or not script.owns(seen, trusted=script in trusted):
            continue
        if script.name == "korean" and latin >= SCRIPT_CONFIDENCE:
            continue
        if best is None or how_sure > best[1]:
            best = (seen.strip(), how_sure)
    if best is not None and (not has_han_or_kana(text) or best[1] >= sure):
        return best
    return text, sure


def _piece(image: Any, box: Any) -> Any:
    h, w = image.shape[:2]
    xs, ys = [float(p[0]) for p in box], [float(p[1]) for p in box]
    pad = REREAD_PAD * (max(ys) - min(ys))
    return image[max(0, int(min(ys) - pad)):min(h, int(max(ys) + pad) + 1),
                 max(0, int(min(xs) - pad)):min(w, int(max(xs) + pad) + 1)]


def reread(image: Any, result: Any, multi: Recognise | None,
           scripts: dict[Script, Recognise] | None = None) -> list[Any]:
    """The main reader's [[box, text, confidence], ...] (all of it, sure or not) with
    each piece read again by the other readers and pick()ed. Ratings aren't read
    again. Pieces nobody is sure of are dropped, as the main reader alone drops them."""
    pieces = []
    for item in result or []:
        try:
            box, text, confidence = item[0], str(item[1]), float(item[2])
            part = _piece(image, box)
        except (TypeError, ValueError, IndexError):
            continue
        if _is_rating(text) and confidence >= MIN_CONFIDENCE or not part.size:
            pieces.append((box, (text, confidence), None, {}))
        else:
            pieces.append((box, (text, confidence), multi(part) if multi is not None else None,
                           {script: recognise(part) for script, recognise in (scripts or {}).items()}))
    # A script read for sure in one piece (Сергей) vouches for it in the others (Москва).
    trusted = frozenset(script for _, main, again, seen in pieces
                        for script, reading in seen.items() if pick(main, again, seen)[0] == reading[0].strip())
    out = []
    for box, main, again, seen in pieces:
        text, confidence = pick(main, again, seen, trusted) if seen or again else main
        if confidence >= MIN_CONFIDENCE:
            out.append([box, text, confidence])
    return out


class TextReader:
    """reader(image) for the timer corner (main reader only); reader.names(image)
    for the name corner (every reader)."""

    def __init__(self, main: Any, multi: Recognise | None = None,
                 scripts: dict[Script, Recognise] | None = None) -> None:
        self.main, self.multi, self.scripts = main, multi, dict(scripts or {})

    def __call__(self, image: Any) -> Any:
        return self.main(image)[0]

    def names(self, image: Any) -> list[Any]:
        if self.multi is None and not self.scripts:
            return self.main(image)[0]
        return reread(image, self.main(image, text_score=0.0)[0], self.multi, self.scripts)

    def describe(self) -> str:
        extra = (["Chinese and Japanese"] if self.multi else []) + [s.label for s in self.scripts]
        return "Latin and Chinese" + (" + " + ", ".join(extra) if extra else " only")


# --- the script models: fetched once, checked every time ---------------------------

MODELS_DIR = Path.home() / ".decklens" / "models"
# https://github.com/PT-Perkasa-Pilar-Utama/ppu-paddle-ocr-models (Apache-2.0): ONNX
# exports of PaddleOCR's PP-OCRv5 mobile recognisers (Apache-2.0). Pinned commit.
MODELS_COMMIT = "47db434bfc5797a04019edf26eba99a3543ada79"
_REPO = "PT-Perkasa-Pilar-Utama/ppu-paddle-ocr-models"
_MODEL_URL = "https://media.githubusercontent.com/media/{repo}/{commit}/recognition/multi/{name}/v5/{name}_PP-OCRv5_mobile_rec_infer.onnx"
_KEYS_URL = "https://raw.githubusercontent.com/{repo}/{commit}/recognition/multi/{name}/v5/ppocrv5_{name}_dict.txt"
MAX_KEYS_BYTES = 1_000_000


class BadDownload(Exception):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch(url: str, sha256: str, max_bytes: int, dest: Path,
          get: Callable[..., Any] | None = None) -> None:
    """Download url to dest only if it is at most max_bytes and has this SHA-256.
    Nothing is left at dest otherwise."""
    if get is None:
        import requests
        get = requests.get
    digest, size = hashlib.sha256(), 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".part")
    try:
        with get(url, stream=True, timeout=(10, 60)) as response:
            response.raise_for_status()
            with partial.open("wb") as f:
                for chunk in response.iter_content(1 << 16):
                    size += len(chunk)
                    if size > max_bytes:
                        raise BadDownload(f"{url} is bigger than expected")
                    digest.update(chunk)
                    f.write(chunk)
        if digest.hexdigest() != sha256:
            raise BadDownload(f"{url} isn't the expected file (SHA-256 differs)")
        os.replace(partial, dest)
    finally:
        partial.unlink(missing_ok=True)


def model_files(script: Script, folder: Path = MODELS_DIR) -> tuple[Path, Path]:
    return folder / f"{script.name}.onnx", folder / f"{script.name}_dict.txt"


def ensure_model(script: Script, folder: Path = MODELS_DIR, *, download: bool = True,
                 get: Callable[..., Any] | None = None) -> tuple[Path, Path] | None:
    """The model and its character list, checked against their pinned hashes;
    fetched first if missing or changed. None if they can't be had."""
    model, keys = model_files(script, folder)
    wanted = ((model, script.model_sha256, script.model_bytes, _MODEL_URL),
              (keys, script.keys_sha256, MAX_KEYS_BYTES, _KEYS_URL))
    said = False
    for path, sha256, max_bytes, url in wanted:
        if path.is_file() and _sha256(path) == sha256:
            continue
        if not download:
            return None
        if not said:
            log.info("fetching the %s text reader (once)...", script.label)
            said = True
        fetch(url.format(repo=_REPO, commit=MODELS_COMMIT, name=script.name), sha256,
              max_bytes, path, get)
    return model, keys


def _keys_for_rapidocr(keys: Path) -> Path:
    """The character list starts with an empty line (the model's "no letter"),
    which RapidOCR adds itself."""
    lines = keys.read_text(encoding="utf-8").split("\n")
    out = keys.with_name(keys.stem + "_keys.txt")
    body = "\n".join(lines[1:] if lines and lines[0] == "" else lines)
    if not out.is_file() or out.read_text(encoding="utf-8") != body:
        out.write_text(body, encoding="utf-8")
    return out


def script_reader(script: Script, model: Path, keys: Path) -> Recognise:
    from rapidocr import ModelType, OCRVersion, RapidOCR

    engine = RapidOCR(params={"Global.log_level": "error", "Rec.model_path": str(model),
                              "Rec.rec_keys_path": str(_keys_for_rapidocr(keys)),
                              "Rec.ocr_version": OCRVersion.PPOCRV5, "Rec.model_type": ModelType.MOBILE})

    def recognise(part: Any) -> Read:
        found = engine(part, use_det=False, use_cls=False, use_rec=True)
        texts, scores = getattr(found, "txts", None), getattr(found, "scores", None)
        if not texts or not scores:
            return "", 0.0
        text = str(texts[0])
        return (visual_to_logical(text) if script.rtl else text), float(scores[0])
    return recognise


def multilingual_reader() -> Recognise | None:
    """Reads one piece of text with the multilingual model, or None if the rapidocr
    package isn't installed."""
    try:
        from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR
    except ImportError:
        log.warning("names in other scripts need more text readers: "
                    'run  pip install -e ".[watcher]"  again')
        return None
    # The model shipped inside the package, so nothing is downloaded.
    engine = RapidOCR(params={"Global.log_level": "error", "Rec.ocr_version": OCRVersion.PPOCRV6,
                              "Rec.model_type": ModelType.SMALL, "Rec.lang_type": LangRec.JAPAN})

    def recognise(part: Any) -> Read:
        found = engine(part, use_det=False, use_cls=False, use_rec=True)
        texts, scores = getattr(found, "txts", None), getattr(found, "scores", None)
        return (str(texts[0]), float(scores[0])) if texts and scores else ("", 0.0)
    return recognise


def script_readers(folder: Path = MODELS_DIR, *, download: bool = True) -> dict[Script, Recognise]:
    readers: dict[Script, Recognise] = {}
    try:
        import rapidocr  # noqa: F401
    except ImportError:
        return readers
    for script in SCRIPTS:
        try:
            files = ensure_model(script, folder, download=download)
        except (OSError, BadDownload) as exc:  # requests' errors are OSErrors too
            log.warning("couldn't fetch the %s text reader (%s); %s names won't be read "
                        "this time", script.label, exc, script.label)
            continue
        if files is not None:
            readers[script] = script_reader(script, *files)
    return readers


def text_reader() -> TextReader:
    from rapidocr_onnxruntime import RapidOCR

    return TextReader(RapidOCR(), multilingual_reader(), script_readers())
