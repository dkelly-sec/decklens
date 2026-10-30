"""DeckLens web app: a small API plus a static page.

    python -m decklens.web          # http://127.0.0.1:8000

The browser only ever talks to this server. The Clash Royale API key stays
server-side, every input is validated, every route is rate-limited, upstream
results are cached, and responses carry a strict Content Security Policy.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any, Callable, Iterator, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

from ..api_client import ApiError, ClashRoyaleClient, NotFoundError
from ..archetypes import ArchetypeModel
from ..model import LinearMatchupModel
from .security import (
    BodySizeLimitMiddleware, RateLimiter, SecurityHeadersMiddleware, TTLCache,
)
from .services import (
    Predictor, battle_mix, card_ids, last_opponent, recent_decks, safe_tag, summarise_player,
    validate_deck,
)

log = logging.getLogger("decklens.web")

RATE_LIMITS = {
    "lookup": (30, 60.0),   # player / battle-log lookups: 30 a minute per client
    "search": (20, 60.0),   # name search: 20 a minute (slows down scraping the index)
    "predict": (60, 60.0),
    "images": (600, 60.0),  # card art: a page shows up to ~40 cards at once
}
STATIC_DIR = Path(str(resources.files("decklens") / "web" / "static"))


@dataclass
class Services:
    client: Any                                   # ClashRoyaleClient (or a fake in tests)
    predictor: Predictor | None = None
    search_db: Callable[[], Any] | None = None    # returns a context manager yielding a connection
    images: Any = None                            # CardImages
    limiter: RateLimiter = field(default_factory=lambda: RateLimiter(RATE_LIMITS))
    cache: TTLCache = field(default_factory=lambda: TTLCache(ttl=60.0, max_items=2000))


class PredictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")  # unknown fields are rejected, not ignored
    deck_a: list[int]
    deck_b: list[int]


def create_app(services: Services, *, hsts: bool = False) -> FastAPI:
    # No interactive docs or schema in production: less surface, nothing to probe.
    app = FastAPI(title="DeckLens", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=4096)
    app.add_middleware(SecurityHeadersMiddleware, hsts=hsts)

    def limit(bucket: str) -> Callable[[Request], None]:
        def check(request: Request) -> None:
            client = request.client.host if request.client else "unknown"
            wait = services.limiter.check(client, bucket)
            if wait is not None:
                raise HTTPException(429, "Too many requests, slow down",
                                    headers={"Retry-After": str(int(wait) + 1)})
        return check

    def upstream(kind: str, tag: str) -> Any:
        fetch = services.client.get_player if kind == "player" else services.client.get_battle_log
        try:
            return services.cache.get_or_set((kind, tag), lambda: fetch(tag))
        except NotFoundError:
            raise HTTPException(404, "No player with that tag") from None
        except ApiError as exc:
            if exc.status == 403:
                log.error("Clash Royale API rejected our key (check the key and its IP whitelist)")
                raise HTTPException(503, "Lookups are unavailable right now") from None
            log.warning("Clash Royale API error: %s", exc)
            raise HTTPException(502, "The Clash Royale API isn't responding") from None

    def tag_or_400(raw: str) -> str:
        try:
            return safe_tag(raw)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    def predictor() -> Predictor:
        if services.predictor is None:
            raise HTTPException(503, "No trained model yet: run python -m decklens.train")
        return services.predictor

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error on %s", request.url.path)
        return JSONResponse({"detail": "Something went wrong"}, status_code=500)

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "model": services.predictor is not None,
                "search": services.search_db is not None}

    @app.get("/api/players/{tag}", dependencies=[Depends(limit("lookup"))])
    def player(tag: str) -> dict[str, Any]:
        return summarise_player(upstream("player", tag_or_400(tag)))

    @app.get("/api/players/{tag}/decks", dependencies=[Depends(limit("lookup"))])
    def decks(tag: str) -> dict[str, Any]:
        tag = tag_or_400(tag)
        log_entries = upstream("battlelog", tag)
        # Decks per mode (Ranked, 20-win challenge), plus what they've been
        # playing overall so an empty tab can say why.
        return {"tag": tag, "modes": recent_decks(tag, log_entries),
                "recent_modes": battle_mix(log_entries)}

    @app.get("/api/players/{tag}/last-opponent", dependencies=[Depends(limit("lookup"))])
    def last_opponent_route(tag: str) -> dict[str, Any]:
        tag = tag_or_400(tag)
        found = last_opponent(tag, upstream("battlelog", tag))
        if found is None:
            raise HTTPException(404, "No recent Ranked or 20-win challenge battles")
        your = card_ids(found["your_deck"]["cards"])
        theirs = card_ids(found["deck"]["cards"])
        if services.predictor is not None and min(len(your), len(theirs)) >= 7:
            found["matchup"] = services.predictor.predict(your, theirs)
        return found

    @app.get("/api/search", dependencies=[Depends(limit("search"))])
    def search(name: str = Query(min_length=1, max_length=40),
               near_rating: int | None = Query(default=None, ge=0, le=20_000)) -> dict[str, Any]:
        if services.search_db is None:
            raise HTTPException(503, "Search isn't set up on this server")
        import psycopg

        from .search import search_players

        try:
            with services.search_db() as conn:
                return {"results": search_players(conn, name, near_rating=near_rating)}
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        except psycopg.OperationalError as exc:  # database down or wrong login
            log.warning("player search database unavailable: %s", type(exc).__name__)
            raise HTTPException(503, "Search is unavailable right now") from None

    @app.post("/api/predict", dependencies=[Depends(limit("predict"))])
    def predict(body: PredictRequest, model: Predictor = Depends(predictor)) -> dict[str, Any]:
        known = model.known_cards()
        try:
            deck_a, deck_b = validate_deck(body.deck_a, known), validate_deck(body.deck_b, known)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        return model.predict(deck_a, deck_b)

    @app.get("/cards/{card_id}.png", include_in_schema=False,
             dependencies=[Depends(limit("images"))])
    def card_image(card_id: int, form: Literal["evo", "hero"] | None = None) -> Response:
        if services.images is None or not 0 < card_id < 10**9:
            raise HTTPException(404, "No image")
        try:
            data = services.images.image(card_id, form=form)
        except ApiError:
            data = None  # card list unavailable: the page falls back to card names
        if data is None:
            raise HTTPException(404, "No image")
        return Response(data, media_type="image/png",
                        headers={"Cache-Control": "public, max-age=86400"})

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


# --- wiring from the environment ------------------------------------------------


def _latest(model_dir: Path, prefix: str) -> Path | None:
    files = sorted(model_dir.glob(f"{prefix}-*.json"))
    return files[-1] if files else None


def load_predictor(model_dir: Path) -> Predictor | None:
    """Newest trained models from model_dir (plain JSON, safe to load)."""
    archetype_file = _latest(model_dir, "decklens-archetypes")
    if archetype_file is None:
        return None
    linear_file = _latest(model_dir, "decklens-linear")
    linear = None
    if linear_file is not None:
        try:
            linear = LinearMatchupModel.from_json(linear_file.read_text(encoding="utf-8"))
        except (ValueError, KeyError):
            log.warning("ignoring %s: retrain to update it", linear_file.name)
    return Predictor(ArchetypeModel.from_json(archetype_file.read_text(encoding="utf-8")), linear)


def create_app_from_env() -> FastAPI:
    from ..config import load_settings

    settings = load_settings(require_database=False)
    search_url = os.environ.get("WEB_DATABASE_URL") or settings.database_url
    if search_url and "replace-me" in search_url:
        log.warning("database URL still has the replace-me placeholder: search is off")
        search_url = None
    search_db = None
    if search_url:
        import psycopg

        @contextmanager
        def connect() -> Iterator[Any]:
            with psycopg.connect(search_url, connect_timeout=5) as conn:
                conn.read_only = True  # belt and braces on top of the read-only role
                yield conn

        search_db = connect

    model_dir = Path(os.environ.get("DECKLENS_MODEL_DIR", "models"))
    predictor = load_predictor(model_dir)
    if predictor is None:
        log.warning("no trained models in %s: predictions are off until you train", model_dir)
    elif search_db is not None:
        from .tiers import TierStats, load_rows

        def rows() -> list[tuple]:
            with search_db() as conn:
                return load_rows(conn)

        predictor.tiers = TierStats(predictor.archetypes, rows)

    from .cards import CardImages

    client = ClashRoyaleClient(settings.api_token, timeout=settings.request_timeout)
    return create_app(Services(client=client, predictor=predictor, search_db=search_db,
                               images=CardImages(client)),
                      hsts=os.environ.get("DECKLENS_HTTPS") == "1")
