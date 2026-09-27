import os
import pickle
import random
import re
import socket
import struct
import time
from typing import Optional, List, Dict, Any, Tuple

import numpy as np
import pandas as pd
import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv



# =========================
# ENV
# =========================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))
TMDB_API_KEY = (os.getenv("TMDB_API_KEY") or "").strip() or None
TMDB_ACCESS_TOKEN = (os.getenv("TMDB_ACCESS_TOKEN") or "").strip() or None

TMDB_HOST = "api.themoviedb.org"
TMDB_IMG_500 = "https://image.tmdb.org/t/p/w500"

TMDB_RETRY_AT = 0.0
TMDB_RETRY_COOLDOWN = 30

# Some networks (this one included) answer api.themoviedb.org with an
# address that never connects. Look the host up on public DNS instead.
_PUBLIC_DNS_SERVERS = ("1.1.1.1", "8.8.8.8")
_DNS_CACHE: Dict[str, Tuple[float, List[str]]] = {}
_DNS_TTL = 300.0
_PREFERRED_TMDB_IP: Optional[str] = None


def _read_dns_name(data: bytes, offset: int) -> Tuple[int, bytes]:
    parts: List[bytes] = []
    hopped = False
    end = offset
    seen = set()
    while offset < len(data):
        length = data[offset]
        if length == 0:
            offset += 1
            if not hopped:
                end = offset
            break
        if length & 0xC0 == 0xC0:
            if offset + 1 >= len(data):
                break
            pointer = ((length & 0x3F) << 8) | data[offset + 1]
            if pointer in seen or pointer >= len(data):
                break
            seen.add(pointer)
            if not hopped:
                end = offset + 2
            hopped = True
            offset = pointer
            continue
        offset += 1
        parts.append(data[offset : offset + length])
        offset += length
        if not hopped:
            end = offset
    return end, b".".join(parts)


def _query_dns_a(hostname: str, server: str, timeout: float = 2.5) -> List[str]:
    tid = random.randint(0, 65535)
    header = struct.pack("!HHHHHH", tid, 0x0100, 1, 0, 0, 0)
    qname = b"".join(
        bytes([len(label)]) + label.encode("ascii") for label in hostname.split(".")
    ) + b"\x00"
    packet = header + qname + struct.pack("!HH", 1, 1)

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        sock.sendto(packet, (server, 53))
        data, _ = sock.recvfrom(4096)

    if len(data) < 12:
        return []
    rid, flags, qdcount, ancount, _, _ = struct.unpack("!HHHHHH", data[:12])
    if rid != tid or (flags & 0x000F):
        return []

    offset = 12
    for _ in range(qdcount):
        offset, _ = _read_dns_name(data, offset)
        offset += 4

    ips: List[str] = []
    for _ in range(ancount):
        offset, _ = _read_dns_name(data, offset)
        if offset + 10 > len(data):
            break
        rtype, rclass, _, rdlen = struct.unpack("!HHIH", data[offset : offset + 10])
        offset += 10
        rdata = data[offset : offset + rdlen]
        offset += rdlen
        if rtype == 1 and rclass == 1 and len(rdata) == 4:
            ips.append(".".join(str(b) for b in rdata))
    return ips


def _public_dns_addresses(hostname: str) -> List[str]:
    now = time.monotonic()
    cached = _DNS_CACHE.get(hostname)
    if cached and cached[0] > now:
        return list(cached[1])

    ips: List[str] = []
    for server in _PUBLIC_DNS_SERVERS:
        try:
            ips = _query_dns_a(hostname, server)
        except OSError:
            ips = []
        if ips:
            break

    if not ips:
        try:
            infos = socket.getaddrinfo(hostname, 443, socket.AF_INET, socket.SOCK_STREAM)
            ips = list(dict.fromkeys(info[4][0] for info in infos))
        except OSError:
            ips = []

    if ips:
        _DNS_CACHE[hostname] = (now + _DNS_TTL, ips)
    return ips


# =========================
# FASTAPI APP
# =========================
app = FastAPI(title="Movie Recommender API", version="3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # for local streamlit
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =========================
# PICKLE GLOBALS
# =========================
DF_PATH = os.path.join(BASE_DIR, "df.pkl")
INDICES_PATH = os.path.join(BASE_DIR, "indices.pkl")
TFIDF_MATRIX_PATH = os.path.join(BASE_DIR, "tfidf_matrix.pkl")

df: Optional[pd.DataFrame] = None
indices_obj: Any = None
tfidf_matrix: Any = None

TITLE_TO_IDX: Optional[Dict[str, int]] = None


# =========================
# MODELS
# =========================
class TMDBMovieCard(BaseModel):
    tmdb_id: int
    title: str
    poster_url: Optional[str] = None
    release_date: Optional[str] = None
    vote_average: Optional[float] = None


class TMDBMovieDetails(BaseModel):
    tmdb_id: int
    title: str
    overview: Optional[str] = None
    release_date: Optional[str] = None
    poster_url: Optional[str] = None
    backdrop_url: Optional[str] = None
    genres: List[dict] = []


class TFIDFRecItem(BaseModel):
    title: str
    score: float
    tmdb: Optional[TMDBMovieCard] = None
    from_catalog: bool = True


class SearchBundleResponse(BaseModel):
    query: str
    movie_details: TMDBMovieDetails
    tfidf_recommendations: List[TFIDFRecItem]
    genre_recommendations: List[TMDBMovieCard]


# =========================
# UTILS
# =========================
def _norm_title(t: str) -> str:
    return str(t).strip().lower()


def make_img_url(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    return f"{TMDB_IMG_500}{path}"


async def tmdb_get(path: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Safe TMDB GET:
    - Network errors -> 503
    - TMDB API errors -> 502 with detail

    Connects to an address from public DNS. The machine's resolver answers
    api.themoviedb.org with an address that never accepts a connection.
    """
    global TMDB_RETRY_AT, _PREFERRED_TMDB_IP
    if not TMDB_ACCESS_TOKEN and not TMDB_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="Configure TMDB_ACCESS_TOKEN or TMDB_API_KEY in backend/.env",
        )
    if time.monotonic() < TMDB_RETRY_AT:
        raise HTTPException(status_code=503, detail="TMDB is temporarily unavailable")

    q = dict(params)
    headers = {"Host": TMDB_HOST}
    if TMDB_ACCESS_TOKEN:
        headers["Authorization"] = f"Bearer {TMDB_ACCESS_TOKEN}"
    else:
        q["api_key"] = TMDB_API_KEY

    ips = _public_dns_addresses(TMDB_HOST)
    if _PREFERRED_TMDB_IP:
        ips = [_PREFERRED_TMDB_IP] + [ip for ip in ips if ip != _PREFERRED_TMDB_IP]
    if not ips:
        TMDB_RETRY_AT = time.monotonic() + TMDB_RETRY_COOLDOWN
        raise HTTPException(status_code=503, detail="TMDB request error: DNS lookup failed")

    last_error: Optional[BaseException] = None
    response: Optional[httpx.Response] = None
    async with httpx.AsyncClient(timeout=httpx.Timeout(12.0, connect=6.0)) as client:
        for ip in ips:
            try:
                response = await client.get(
                    f"https://{ip}/3{path}",
                    params=q,
                    headers=headers,
                    extensions={"sni_hostname": TMDB_HOST},
                )
                _PREFERRED_TMDB_IP = ip
                break
            except httpx.RequestError as error:
                last_error = error
                if ip == _PREFERRED_TMDB_IP:
                    _PREFERRED_TMDB_IP = None

    if response is None:
        _DNS_CACHE.pop(TMDB_HOST, None)
        TMDB_RETRY_AT = time.monotonic() + TMDB_RETRY_COOLDOWN
        raise HTTPException(
            status_code=503,
            detail=f"TMDB request error: {type(last_error).__name__} | {last_error!r}",
        )

    r = response

    if r.status_code != 200:
        if r.status_code >= 500 or r.status_code in {401, 403}:
            TMDB_RETRY_AT = time.monotonic() + TMDB_RETRY_COOLDOWN
        raise HTTPException(
            status_code=502, detail=f"TMDB error {r.status_code}: {r.text}"
        )

    return r.json()


def local_movie_card(index: int, movie: pd.Series) -> TMDBMovieCard:
    vote_average = pd.to_numeric(movie.get("vote_average"), errors="coerce")
    return TMDBMovieCard(
        tmdb_id=index + 1,
        title=str(movie.get("title") or ""),
        vote_average=float(vote_average) if pd.notna(vote_average) else None,
    )


def local_movie_details(index: int) -> TMDBMovieDetails:
    if df is None or index < 0 or index >= len(df):
        raise HTTPException(status_code=404, detail="Movie not found in local catalog")

    movie = df.iloc[index]
    raw_genres = movie.get("genres")
    genre_names = [
        genre.strip()
        for genre in str(raw_genres or "").split(",")
        if genre.strip() and genre.strip().lower() != "nan"
    ]
    overview = movie.get("overview")
    return TMDBMovieDetails(
        tmdb_id=index + 1,
        title=str(movie.get("title") or ""),
        overview=str(overview) if pd.notna(overview) and str(overview).strip() else None,
        genres=[{"id": genre_id, "name": name} for genre_id, name in enumerate(genre_names, 1)],
    )


def local_movie_search(query: str, limit: int = 20) -> List[dict]:
    if df is None or "title" not in df.columns:
        return []

    matches = df["title"].astype(str).str.contains(query, case=False, regex=False, na=False)
    results = df.loc[matches].copy()
    if "popularity" in results.columns:
        results["_popularity"] = pd.to_numeric(results["popularity"], errors="coerce").fillna(0)
        results = results.sort_values("_popularity", ascending=False, kind="stable")

    return [
        {
            "id": int(index) + 1,
            "title": str(movie.get("title") or ""),
            "poster_path": None,
            "release_date": None,
            "vote_average": (
                float(vote) if pd.notna(vote := pd.to_numeric(movie.get("vote_average"), errors="coerce")) else None
            ),
            "overview": movie.get("overview"),
        }
        for index, movie in results.head(limit).iterrows()
    ]


def local_genre_recommendations(genre: str, limit: int, exclude_id: int) -> List[TMDBMovieCard]:
    if df is None or "genres" not in df.columns:
        return []

    matches = df["genres"].astype(str).str.contains(genre, case=False, regex=False, na=False)
    results = df.loc[matches].copy()
    if "popularity" in results.columns:
        results["_popularity"] = pd.to_numeric(results["popularity"], errors="coerce").fillna(0)
        results = results.sort_values("_popularity", ascending=False, kind="stable")

    return [
        local_movie_card(int(index), movie)
        for index, movie in results.iterrows()
        if int(index) + 1 != exclude_id
    ][:limit]


def local_card_by_title(title: str) -> Optional[TMDBMovieCard]:
    if df is None or TITLE_TO_IDX is None:
        return None

    index = TITLE_TO_IDX.get(_norm_title(title))
    if index is None:
        matches = local_movie_search(title, limit=1)
        if not matches:
            return None
        index = int(matches[0]["id"]) - 1
    if index < 0 or index >= len(df):
        return None
    return local_movie_card(index, df.iloc[index])


async def movie_details_with_fallback(movie_id: int) -> TMDBMovieDetails:
    try:
        return await tmdb_movie_details(movie_id)
    except HTTPException as error:
        if error.status_code not in {502, 503}:
            raise
        return local_movie_details(movie_id - 1)


async def movie_search_with_fallback(query: str, page: int = 1) -> Dict[str, Any]:
    try:
        return await tmdb_search_movies(query=query, page=page)
    except HTTPException as error:
        if error.status_code not in {502, 503}:
            raise
        page_size = 20
        return {
            "page": page,
            "results": local_movie_search(query, limit=page_size * page)[page_size * (page - 1) :],
            "total_pages": 1,
            "total_results": len(local_movie_search(query, limit=1000)),
        }


async def movie_search_first_with_fallback(query: str) -> Optional[dict]:
    data = await movie_search_with_fallback(query=query, page=1)
    results = data.get("results", [])
    return results[0] if results else None


async def tmdb_cards_from_results(
    results: List[dict], limit: int = 20
) -> List[TMDBMovieCard]:
    out: List[TMDBMovieCard] = []
    for m in (results or [])[:limit]:
        out.append(
            TMDBMovieCard(
                tmdb_id=int(m["id"]),
                title=m.get("title") or m.get("name") or "",
                poster_url=make_img_url(m.get("poster_path")),
                release_date=m.get("release_date"),
                vote_average=m.get("vote_average"),
            )
        )
    return out


async def tmdb_movie_details(movie_id: int) -> TMDBMovieDetails:
    data = await tmdb_get(f"/movie/{movie_id}", {"language": "en-US"})
    return TMDBMovieDetails(
        tmdb_id=int(data["id"]),
        title=data.get("title") or "",
        overview=data.get("overview"),
        release_date=data.get("release_date"),
        poster_url=make_img_url(data.get("poster_path")),
        backdrop_url=make_img_url(data.get("backdrop_path")),
        genres=data.get("genres", []) or [],
    )


async def tmdb_search_movies(query: str, page: int = 1) -> Dict[str, Any]:
    """
    Raw TMDB response for keyword search (MULTIPLE results).
    Streamlit will use this for suggestions and grid.
    """
    return await tmdb_get(
        "/search/movie",
        {
            "query": query,
            "include_adult": "false",
            "language": "en-US",
            "page": page,
        },
    )


async def tmdb_search_first(query: str) -> Optional[dict]:
    data = await tmdb_search_movies(query=query, page=1)
    results = data.get("results", [])
    return results[0] if results else None


# =========================
# TF-IDF Helpers
# =========================
def build_title_to_idx_map(indices: Any) -> Dict[str, int]:
    """
    indices.pkl can be:
    - dict(title -> index)
    - pandas Series (index=title, value=index)
    We normalize into TITLE_TO_IDX.
    """
    title_to_idx: Dict[str, int] = {}

    if isinstance(indices, dict):
        for k, v in indices.items():
            title_to_idx[_norm_title(k)] = int(v)
        return title_to_idx

    # pandas Series or similar mapping
    try:
        for k, v in indices.items():
            title_to_idx[_norm_title(k)] = int(v)
        return title_to_idx
    except Exception:
        # last resort: if it's a list-like etc.
        raise RuntimeError(
            "indices.pkl must be dict or pandas Series-like (with .items())"
        )


def get_local_idx_by_title(title: str) -> int:
    global TITLE_TO_IDX
    if TITLE_TO_IDX is None:
        raise HTTPException(status_code=500, detail="TF-IDF index map not initialized")
    key = _norm_title(title)
    if key in TITLE_TO_IDX:
        return int(TITLE_TO_IDX[key])
    raise HTTPException(
        status_code=404, detail=f"Title not found in local dataset: '{title}'"
    )


def tfidf_recommend_titles(
    query_title: str, top_n: int = 10
) -> List[Tuple[str, float]]:
    """
    Returns list of (title, score) from local df using cosine similarity on TF-IDF matrix.
    Safe against missing columns/rows.
    """
    global df, tfidf_matrix
    if df is None or tfidf_matrix is None:
        raise HTTPException(status_code=500, detail="TF-IDF resources not loaded")

    idx = get_local_idx_by_title(query_title)

    # query vector
    qv = tfidf_matrix[idx]
    scores = (tfidf_matrix @ qv.T).toarray().ravel()

    # sort descending
    order = np.argsort(-scores)

    out: List[Tuple[str, float]] = []
    for i in order:
        if int(i) == int(idx):
            continue
        try:
            title_i = str(df.iloc[int(i)]["title"])
        except Exception:
            continue
        out.append((title_i, float(scores[int(i)])))
        if len(out) >= top_n:
            break
    return out


async def tmdb_story_recommendations(movie_id: int, limit: int) -> List[TFIDFRecItem]:
    """TMDB 'recommendations', then 'similar', for titles missing from the local catalog."""
    for path in (f"/movie/{movie_id}/recommendations", f"/movie/{movie_id}/similar"):
        try:
            data = await tmdb_get(path, {"language": "en-US", "page": 1})
        except HTTPException as error:
            if error.status_code not in {502, 503}:
                raise
            return []
        cards = await tmdb_cards_from_results(data.get("results", []), limit=limit + 1)
        items = [
            TFIDFRecItem(title=card.title, score=0.0, tmdb=card, from_catalog=False)
            for card in cards
            if card.tmdb_id != movie_id and card.title
        ]
        if items:
            return items[:limit]
    return []


async def attach_tmdb_card_by_title(title: str) -> Optional[TMDBMovieCard]:
    """
    Uses TMDB search by title to fetch poster for a local title.
    If not found, returns None (never crashes the endpoint).
    """
    try:
        m = await tmdb_search_first(title)
        if not m:
            return local_card_by_title(title)
        return TMDBMovieCard(
            tmdb_id=int(m["id"]),
            title=m.get("title") or title,
            poster_url=make_img_url(m.get("poster_path")),
            release_date=m.get("release_date"),
            vote_average=m.get("vote_average"),
        )
    except HTTPException as error:
        if error.status_code not in {502, 503}:
            raise
        return local_card_by_title(title)


# =========================
# STARTUP: LOAD PICKLES
# =========================
@app.on_event("startup")
def load_pickles():
    global df, indices_obj, tfidf_matrix, TITLE_TO_IDX

    # Load df
    with open(DF_PATH, "rb") as f:
        df = pickle.load(f)

    # Load indices
    with open(INDICES_PATH, "rb") as f:
        indices_obj = pickle.load(f)

    # Load TF-IDF matrix (usually scipy sparse)
    with open(TFIDF_MATRIX_PATH, "rb") as f:
        tfidf_matrix = pickle.load(f)

    # Build normalized map
    TITLE_TO_IDX = build_title_to_idx_map(indices_obj)

    # sanity
    if df is None or "title" not in df.columns:
        raise RuntimeError("df.pkl must contain a DataFrame with a 'title' column")


# =========================
# ROUTES
# =========================
@app.get("/health")
def health():
    return {
        "status": "ok",
        "catalog_loaded": df is not None,
        "tmdb_configured": bool(TMDB_ACCESS_TOKEN or TMDB_API_KEY),
        "tmdb_available": (
            bool(TMDB_ACCESS_TOKEN or TMDB_API_KEY)
            and time.monotonic() >= TMDB_RETRY_AT
        ),
    }


# ---------- HOME FEED (TMDB) ----------
@app.get("/home", response_model=List[TMDBMovieCard])
async def home(
    category: str = Query("popular"),
    limit: int = Query(24, ge=1, le=50),
):
    """
    Home feed for Streamlit (posters).
    category:
      - trending (trending/movie/day)
      - popular, top_rated, upcoming, now_playing  (movie/{category})
    """
    try:
        if category not in {"popular", "top_rated", "upcoming", "now_playing"}:
            if category != "trending":
                raise HTTPException(status_code=400, detail="Invalid category")

        path = f"/{category}/movie/day" if category == "trending" else f"/movie/{category}"
        params = {"language": "en-US"}
        if category != "trending":
            params["page"] = 1
        data = await tmdb_get(path, params)
        return await tmdb_cards_from_results(data.get("results", []), limit=limit)

    except HTTPException as error:
        if error.status_code not in {502, 503}:
            raise
        if df is None:
            raise
        sort_column = "vote_average" if category == "top_rated" else "popularity"
        movies = df.copy()
        if sort_column in movies.columns:
            movies["_sort_value"] = pd.to_numeric(movies[sort_column], errors="coerce").fillna(0)
            movies = movies.sort_values("_sort_value", ascending=False, kind="stable")
        return [
            local_movie_card(int(index), movie)
            for index, movie in movies.head(limit).iterrows()
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Home route failed: {e}")


# ---------- TMDB KEYWORD SEARCH (MULTIPLE RESULTS) ----------
@app.get("/tmdb/search")
async def tmdb_search(
    query: str = Query(..., min_length=1),
    page: int = Query(1, ge=1, le=10),
):
    """
    Returns RAW TMDB shape with 'results' list.
    Streamlit will use it for:
      - dropdown suggestions
      - grid results
    """
    return await movie_search_with_fallback(query=query, page=page)


# ---------- MOVIE DETAILS (SAFE ROUTE) ----------
@app.get("/movie/id/{tmdb_id}", response_model=TMDBMovieDetails)
async def movie_details_route(tmdb_id: int):
    return await movie_details_with_fallback(tmdb_id)


# ---------- GENRE RECOMMENDATIONS ----------
@app.get("/recommend/genre", response_model=List[TMDBMovieCard])
async def recommend_genre(
    tmdb_id: int = Query(...),
    limit: int = Query(18, ge=1, le=50),
):
    """
    Given a TMDB movie ID:
    - fetch details
    - pick first genre
    - discover movies in that genre (popular)
    """
    details = await movie_details_with_fallback(tmdb_id)
    if not details.genres:
        return []

    genre = details.genres[0]
    try:
        discover = await tmdb_get(
            "/discover/movie",
            {
                "with_genres": genre["id"],
                "language": "en-US",
                "sort_by": "popularity.desc",
                "page": 1,
            },
        )
        cards = await tmdb_cards_from_results(discover.get("results", []), limit=limit)
    except HTTPException as error:
        if error.status_code not in {502, 503}:
            raise
        cards = local_genre_recommendations(genre["name"], limit, tmdb_id)
    return [c for c in cards if c.tmdb_id != tmdb_id]


# ---------- TF-IDF ONLY (debug/useful) ----------
@app.get("/recommend/tfidf")
async def recommend_tfidf(
    title: str = Query(..., min_length=1),
    top_n: int = Query(10, ge=1, le=50),
):
    recs = tfidf_recommend_titles(title, top_n=top_n)
    return [{"title": t, "score": s} for t, s in recs]


# ---------- BUNDLE: Details + TF-IDF recs + Genre recs ----------
@app.get("/movie/search", response_model=SearchBundleResponse)
async def search_bundle(
    query: str = Query(..., min_length=1),
    tfidf_top_n: int = Query(12, ge=1, le=30),
    genre_limit: int = Query(12, ge=1, le=30),
):
    """
    This endpoint is for when you have a selected movie and want:
      - movie details
      - TF-IDF recommendations (local) + posters
      - Genre recommendations (TMDB) + posters

    NOTE:
    - It selects the BEST match from TMDB for the given query.
    - If you want MULTIPLE matches, use /tmdb/search
    """
    best = await movie_search_first_with_fallback(query)
    if not best:
        raise HTTPException(
            status_code=404, detail=f"No TMDB movie found for query: {query}"
        )

    tmdb_id = int(best["id"])
    details = await movie_details_with_fallback(tmdb_id)

    # 1) TF-IDF recommendations (never crash endpoint)
    tfidf_items: List[TFIDFRecItem] = []

    recs: List[Tuple[str, float]] = []
    try:
        # try local dataset by TMDB title
        recs = tfidf_recommend_titles(details.title, top_n=tfidf_top_n)
    except Exception:
        # fallback to user query
        try:
            recs = tfidf_recommend_titles(query, top_n=tfidf_top_n)
        except Exception:
            recs = []

    for title, score in recs:
        card = await attach_tmdb_card_by_title(title)
        tfidf_items.append(TFIDFRecItem(title=title, score=score, tmdb=card))

    # Titles newer than the local catalog have no TF-IDF neighbors.
    # TMDB recommendations fill the same "similar story" row.
    if not tfidf_items:
        tfidf_items = await tmdb_story_recommendations(tmdb_id, limit=tfidf_top_n)

    # 2) Genre recommendations (TMDB discover by first genre)
    genre_recs: List[TMDBMovieCard] = []
    if details.genres:
        genre = details.genres[0]
        try:
            discover = await tmdb_get(
                "/discover/movie",
                {
                    "with_genres": genre["id"],
                    "language": "en-US",
                    "sort_by": "popularity.desc",
                    "page": 1,
                },
            )
            cards = await tmdb_cards_from_results(
                discover.get("results", []), limit=genre_limit
            )
        except HTTPException as error:
            if error.status_code not in {502, 503}:
                raise
            cards = local_genre_recommendations(
                genre["name"], genre_limit, details.tmdb_id
            )
        genre_recs = [c for c in cards if c.tmdb_id != details.tmdb_id]

    return SearchBundleResponse(
        query=query,
        movie_details=details,
        tfidf_recommendations=tfidf_items,
        genre_recommendations=genre_recs,
    )

