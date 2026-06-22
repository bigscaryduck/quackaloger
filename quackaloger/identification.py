"""Audible search, Audnexus fetch, AI-assisted matching, fuzzy scoring, and caching."""

import json
import os
import re
import time
from difflib import SequenceMatcher
from typing import Optional

from quackaloger.config import Config
from quackaloger.constants import (
    AUDIBLE_CATALOG_URL,
    AUDNEXUS_BOOK_URL,
    DEFAULT_AUDIBLE_REQUEST_DELAY,
    DEFAULT_MAX_AUDIBLE_CANDIDATES,
)
from quackaloger.llm import ExtractError
from quackaloger.models import AudibleMatch, Book
from quackaloger.ui import ui

try:
    import requests as _requests
except ImportError:
    _requests = None


ASIN_PICK_SCHEMA = {
    "type": "object",
    "properties": {
        "asin": {
            "type": "string",
            "description": "10-character Audible ASIN from the candidate list, or empty string if none",
        },
    },
    "required": ["asin"],
}

BOOK_IDENT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "author": {"type": "string"},
        "series": {"type": "string"},
        "sequence": {"type": "string"},
        "narrator": {"type": "string"},
    },
    "required": ["title", "author", "series", "sequence", "narrator"],
}


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def load_cache(tool_dir: str) -> dict:
    cache_path = os.path.join(tool_dir, "cache", "audible.json")
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_cache(tool_dir: str, cache: dict):
    cache_dir = os.path.join(tool_dir, "cache")
    os.makedirs(cache_dir, exist_ok=True)
    cache_path = os.path.join(cache_dir, "audible.json")
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2, ensure_ascii=False)
    except Exception as e:
        ui.warn(f"Could not save audible cache: {e}")


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _fuzzy_score(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _is_valid_asin(val: str) -> bool:
    if not val:
        return False
    val = val.strip().upper()
    return bool(re.match(r"^[A-Z0-9]{10}$", val))


def _match_to_dict(m: AudibleMatch) -> dict:
    return {
        "asin": m.asin, "title": m.title, "subtitle": m.subtitle,
        "author": m.author, "narrator": m.narrator, "series": m.series,
        "sequence": m.sequence, "year": m.year, "description": m.description,
        "genres": m.genres, "duration_min": m.duration_min, "confidence": m.confidence,
    }


# ---------------------------------------------------------------------------
# Audible / Audnexus API calls
# ---------------------------------------------------------------------------

def _search_audible_catalog(
    title: str,
    author: str = None,
    catalog_url: str = AUDIBLE_CATALOG_URL,
    verbose: bool = False,
) -> list:
    """Search Audible catalog API. Returns list of product dicts."""
    if _requests is None:
        return []
    params = {
        "num_results": "10",
        "products_sort_by": "Relevance",
        "title": title,
    }
    if author:
        params["author"] = author

    if verbose:
        ui.verbose(f"[AUDIBLE] Searching: title='{title}', author='{author}'")

    try:
        resp = _requests.get(catalog_url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        products = data.get("products", [])
        if verbose:
            ui.verbose(f"[AUDIBLE] Got {len(products)} results")
        return products
    except Exception as e:
        if verbose:
            ui.verbose(f"[AUDIBLE] Search failed: {e}")
        return []


def _fetch_audnexus_book(
    asin: str,
    region: str = "us",
    audnexus_url: str = AUDNEXUS_BOOK_URL,
    verbose: bool = False,
) -> dict:
    """Fetch full book metadata from Audnexus by ASIN."""
    if _requests is None:
        return {}
    url = f"{audnexus_url}/{asin}"
    params = {"region": region}
    if verbose:
        ui.verbose(f"[AUDNEXUS] Fetching ASIN {asin}")

    try:
        resp = _requests.get(url, params=params, timeout=10)
        if resp.status_code == 404:
            if verbose:
                ui.verbose(f"[AUDNEXUS] ASIN {asin} not found")
            return {}
        if resp.status_code == 400:
            try:
                err = resp.json().get("error", {})
                msg = err.get("message", "Unknown 400 error")
                if verbose:
                    ui.verbose(f"[AUDNEXUS] ASIN {asin} rejected: {msg}")
            except Exception:
                if verbose:
                    ui.verbose(f"[AUDNEXUS] ASIN {asin} rejected (400 Bad Request)")
            return {}
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        if verbose:
            ui.verbose(f"[AUDNEXUS] Fetch failed for {asin}: {e}")
        return {}


def _clean_audible_sequence(sequence: str) -> str:
    if not sequence:
        return ""
    m = re.search(r"\.?\d+(?:\.\d+)?|\d+", sequence)
    return m.group(0) if m else sequence


def _parse_audnexus_to_match(data: dict) -> Optional[AudibleMatch]:
    if not data or not data.get("asin"):
        return None

    series_name = None
    series_seq = None
    if data.get("seriesPrimary"):
        sp = data["seriesPrimary"]
        series_name = sp.get("name")
        series_seq = _clean_audible_sequence(sp.get("position", ""))
    elif data.get("seriesSecondary"):
        ss = data["seriesSecondary"]
        series_name = ss.get("name")
        series_seq = _clean_audible_sequence(ss.get("position", ""))

    authors = data.get("authors", [])
    author_str = ", ".join(a.get("name", "") for a in authors) if authors else None

    narrators = data.get("narrators", [])
    narrator_str = ", ".join(n.get("name", "") for n in narrators) if narrators else None

    genres = []
    for g in data.get("genres", []):
        if g.get("type") == "genre":
            genres.append(g.get("name", ""))

    release = data.get("releaseDate", "")
    year = release.split("-")[0] if release else None

    return AudibleMatch(
        asin=data.get("asin"),
        title=data.get("title"),
        subtitle=data.get("subtitle"),
        author=author_str,
        narrator=narrator_str,
        series=series_name,
        sequence=series_seq,
        year=year,
        description=data.get("summary") or data.get("description"),
        genres=genres,
        duration_min=data.get("runtimeLengthMin", 0) or 0,
    )


def _score_audible_match(
    match: AudibleMatch,
    search_title: str,
    search_author: str,
    expected_book_num: int = None,
) -> float:
    title_score = _fuzzy_score(match.title or "", search_title)
    author_score = 0.0
    if search_author and match.author:
        author_score = _fuzzy_score(match.author, search_author)

    if search_author:
        base = title_score * 0.5 + author_score * 0.3
    else:
        base = title_score * 0.8

    seq_bonus = 0.0
    if expected_book_num is not None and match.sequence:
        try:
            audible_seq = int(float(match.sequence))
            if audible_seq == expected_book_num:
                seq_bonus = 0.2
        except (ValueError, TypeError):
            pass

    return min(base + seq_bonus, 1.0)


# ---------------------------------------------------------------------------
# AI-assisted matching (GPT-4o-mini)
# ---------------------------------------------------------------------------

def _build_ai_picker_prompt(book: Book, candidates: list, max_candidates: int) -> str:
    first = book.files[0]
    lines = [
        "You are matching an audiobook from a local library to Audible search results.",
        "The local audiobook is in ENGLISH. Only match to English-language editions.",
        "",
        "LOCAL METADATA:",
    ]

    if first.filepath:
        lines.append(f"- File path: {first.filepath}")
    if first.tag_artist:
        lines.append(f"- ID3 artist: {first.tag_artist}")
    if first.tag_album_artist and first.tag_album_artist != first.tag_artist:
        lines.append(f"- ID3 album artist: {first.tag_album_artist}")
    if first.tag_album:
        lines.append(f"- ID3 album: {first.tag_album}")
    if first.tag_title:
        lines.append(f"- ID3 title: {first.tag_title}")
    if first.tag_series:
        lines.append(f"- ID3 series: {first.tag_series}")
    if first.tag_series_part:
        lines.append(f"- ID3 series part: {first.tag_series_part}")
    if first.fn_series_hint:
        lines.append(f"- Filename series hint: {first.fn_series_hint}")
    if first.fn_book_number is not None:
        lines.append(f"- Book number (from filename): {first.fn_book_number}")
    if first.path_author_hint:
        lines.append(f"- Folder author: {first.path_author_hint}")
    if first.path_series_hint:
        lines.append(f"- Folder series: {first.path_series_hint}")
    if first.path_title_hint:
        lines.append(f"- Folder title: {first.path_title_hint}")

    lines.append("")
    lines.append("AUDIBLE SEARCH RESULTS:")

    for i, c in enumerate(candidates[:max_candidates], 1):
        parts = [f"{i}. ASIN={c.asin}"]
        parts.append(f'"{c.title}"')
        if c.subtitle:
            parts.append(f'subtitle="{c.subtitle}"')
        if c.author:
            parts.append(f"by {c.author}")
        if c.series:
            series_str = c.series
            if c.sequence:
                series_str += f" #{c.sequence}"
            parts.append(f"| Series: {series_str}")
        if c.narrator:
            parts.append(f"| Narrator: {c.narrator}")
        lines.append("   " + " ".join(parts))

    lines.append("")
    lines.append(
        "Submit the structured result: field 'asin' must be the 10-character ASIN of the best "
        "matching English-language result from the list above, or an empty string if none fit."
    )

    return "\n".join(lines)


def _ai_pick_best_match(
    book: Book,
    candidates: list,
    extract_client,
    max_candidates: int = DEFAULT_MAX_AUDIBLE_CANDIDATES,
    verbose: bool = False,
) -> Optional[str]:
    """Use configured LLM (tool extraction) to pick the best ASIN. Returns ASIN string or None."""
    if not candidates or extract_client is None:
        return None

    prompt = _build_ai_picker_prompt(book, candidates, max_candidates)

    if verbose:
        ui.verbose("[AI] Tool extraction: pick ASIN from candidates")

    try:
        data = extract_client.extract(
            [{"role": "user", "content": prompt}],
            ASIN_PICK_SCHEMA,
            temperature=0.0,
        )
        answer = (data.get("asin") or "").strip().upper()
        if verbose:
            ui.verbose(f"[AI] Response asin field: {answer!r}")

        if answer in ("", "NONE", "NULL"):
            return None

        asin_match = re.search(r"[A-Z0-9]{10}", answer)
        if asin_match:
            picked = asin_match.group(0)
            valid_asins = {c.asin.upper() for c in candidates if c.asin}
            if picked in valid_asins:
                return picked
            if verbose:
                ui.verbose(f"[AI] Returned ASIN {picked} not in candidate list, ignoring")
        return None

    except ExtractError as e:
        if verbose:
            ui.verbose(f"[AI] Extraction failed: {e}")
        return None
    except Exception as e:
        if verbose:
            ui.verbose(f"[AI] LLM call failed: {e}")
        return None


def _ai_identify_book(
    book: Book,
    extract_client,
    verbose: bool = False,
) -> Optional[AudibleMatch]:
    """Use AI to identify a book from local metadata alone when all API lookups fail."""
    if extract_client is None:
        return None

    first = book.files[0]
    lines = [
        "You are identifying an audiobook from a local library to help organize it.",
        "Based on the metadata below, identify this audiobook.",
        "",
        "LOCAL METADATA:",
    ]
    if first.filepath:
        lines.append(f"- File path: {first.filepath}")
    if first.tag_artist:
        lines.append(f"- ID3 artist: {first.tag_artist}")
    if first.tag_album_artist and first.tag_album_artist != first.tag_artist:
        lines.append(f"- ID3 album artist: {first.tag_album_artist}")
    if first.tag_album:
        lines.append(f"- ID3 album: {first.tag_album}")
    if first.tag_title:
        lines.append(f"- ID3 title: {first.tag_title}")
    if first.tag_series:
        lines.append(f"- ID3 series: {first.tag_series}")
    if first.tag_series_part:
        lines.append(f"- ID3 series part: {first.tag_series_part}")
    if first.fn_series_hint:
        lines.append(f"- Filename series hint: {first.fn_series_hint}")
    if first.fn_book_number is not None:
        lines.append(f"- Book number (from filename): {first.fn_book_number}")
    if first.path_author_hint:
        lines.append(f"- Folder author: {first.path_author_hint}")
    if first.path_series_hint:
        lines.append(f"- Folder series: {first.path_series_hint}")
    if first.path_title_hint:
        lines.append(f"- Folder title: {first.path_title_hint}")

    lines.append("")
    lines.append(
        "Return a tool payload with keys title, author, series, sequence, narrator. "
        "Use empty string for unknown optional fields."
    )

    prompt = "\n".join(lines)

    if verbose:
        ui.verbose("[AI] Tool extraction: identify book from local metadata")

    try:
        data = extract_client.extract(
            [{"role": "user", "content": prompt}],
            BOOK_IDENT_SCHEMA,
            temperature=0.0,
        )
        title = (data.get("title") or "").strip()
        if not title:
            return None
        author = (data.get("author") or "").strip() or None
        series = (data.get("series") or "").strip() or None
        sequence = (data.get("sequence") or "").strip() or None
        narrator = (data.get("narrator") or "").strip() or None
        if verbose:
            ui.verbose(f"[AI] title={title!r} author={author!r} series={series!r}")

        return AudibleMatch(
            title=title,
            author=author,
            series=series,
            sequence=sequence,
            narrator=narrator,
            confidence=0.90,
        )

    except ExtractError as e:
        if verbose:
            ui.verbose(f"[AI] Identification extraction failed: {e}")
        return None
    except Exception as e:
        if verbose:
            ui.verbose(f"[AI] Identification failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Main lookup orchestrator
# ---------------------------------------------------------------------------

def lookup_book(book: Book, cache: dict, cfg: Config, extract_client=None) -> Optional[AudibleMatch]:
    """Look up a book on Audible. Returns the best AudibleMatch or None."""
    verbose = cfg.verbosity in ("verbose", "debug")
    delay = cfg.audible_request_delay
    catalog_url = cfg.audible_catalog_url
    audnexus_url = cfg.audnexus_url
    max_candidates = cfg.max_audible_candidates

    # Collect ASINs from ID3 tags
    asins_from_tags = set()
    for f in book.files:
        if f.tag_asin and _is_valid_asin(f.tag_asin):
            asins_from_tags.add(f.tag_asin.strip().upper())

    first = book.files[0]

    base_title = (
        first.tag_album or first.fn_series_hint or first.path_title_hint or first.tag_title or ""
    ).strip()
    search_author = (
        first.tag_artist or first.tag_album_artist or first.path_author_hint or ""
    ).strip()

    # Clean search title
    base_title = re.sub(r"\(.*?\)", "", base_title).strip()
    base_title = re.sub(r"\[.*?\]", "", base_title).strip()
    base_title = re.sub(r"^\d+\s*[-\u2013.]?\s*", "", base_title).strip()
    base_title = re.sub(r"^(?:Vol\.?|Volume|Book)\s*\d+\s*[-\u2013.]\s*", "", base_title, flags=re.IGNORECASE)
    base_title = re.sub(r"\s*[-\u2013]\s*(?:Vol\.?|Volume|Book)\s*\d+\s*$", "", base_title, flags=re.IGNORECASE)
    base_title = re.sub(r"\.macmillan\.", " ", base_title, flags=re.IGNORECASE)
    base_title = re.sub(r"\.?readnfo\b", "", base_title, flags=re.IGNORECASE)
    base_title = re.sub(r"\s+", " ", base_title).strip()

    # Clean search author
    search_author = re.sub(r"\(.*?\)", "", search_author).strip()
    search_author = re.sub(r"/.*$", "", search_author).strip()
    search_author = re.sub(r"\bet\.?\s*al\.?\b", "", search_author, flags=re.IGNORECASE).strip()
    search_author = re.sub(r"\s+", " ", search_author).strip()

    if first.fn_book_number is not None:
        search_title = f"{base_title} {first.fn_book_number}"
    else:
        search_title = base_title

    if not search_title and not asins_from_tags:
        if verbose:
            ui.verbose("[AUDIBLE] No title or ASIN available, skipping lookup")
        return None

    # Strategy 1: Direct ASIN lookup
    for asin in asins_from_tags:
        if asin in cache:
            if verbose:
                ui.verbose(f"[AUDIBLE] Cache hit for ASIN {asin}")
            cached = cache[asin]
            if cached:
                match = AudibleMatch(**cached)
                match.confidence = 1.0
                return match
            continue

        time.sleep(delay)
        data = _fetch_audnexus_book(asin, audnexus_url=audnexus_url, verbose=verbose)
        if data:
            match = _parse_audnexus_to_match(data)
            if match:
                match.confidence = 1.0
                cache[asin] = _match_to_dict(match)
                return match
        cache[asin] = None

    # Strategy 2: Search by title + author
    cache_key = f"search:{search_title.lower()}|{search_author.lower()}"
    if cache_key in cache:
        cached = cache[cache_key]
        if cached:
            if verbose:
                ui.verbose(f"[AUDIBLE] Cache hit for search '{search_title}' by '{search_author}'")
            return AudibleMatch(**cached)
        if not extract_client:
            if verbose:
                ui.verbose(f"[AUDIBLE] Cache says no result for '{search_title}'")
            return None
        if verbose:
            ui.verbose("[AUDIBLE] Cache says no result, but AI available -- retrying")

    if not search_title:
        return None

    time.sleep(delay)
    products = _search_audible_catalog(search_title, search_author, catalog_url=catalog_url, verbose=verbose)

    if not products and search_title != base_title:
        if verbose:
            ui.verbose(f"[AUDIBLE] No results, retrying without book number: '{base_title}'")
        time.sleep(delay)
        products = _search_audible_catalog(base_title, search_author, catalog_url=catalog_url, verbose=verbose)

    if not products and search_author:
        if verbose:
            ui.verbose(f"[AUDIBLE] No results, retrying title-only: '{base_title}'")
        time.sleep(delay)
        products = _search_audible_catalog(base_title, None, catalog_url=catalog_url, verbose=verbose)

    if not products:
        if extract_client:
            ai_match = _ai_identify_book(book, extract_client, verbose=verbose)
            if ai_match:
                cache[cache_key] = _match_to_dict(ai_match)
                return ai_match
        cache[cache_key] = None
        return None

    # Fetch full metadata for top candidates
    max_cand = max_candidates if extract_client else 3
    candidates = []
    for product in products[:max_cand]:
        product_asin = product.get("asin")
        if not product_asin:
            continue

        if product_asin in cache and cache[product_asin]:
            match = AudibleMatch(**cache[product_asin])
        else:
            time.sleep(delay)
            data = _fetch_audnexus_book(product_asin, audnexus_url=audnexus_url, verbose=verbose)
            match = _parse_audnexus_to_match(data) if data else None
            if match:
                cache[product_asin] = _match_to_dict(match)
            elif product.get("title"):
                authors = product.get("authors", [])
                author_str = ", ".join(a.get("name", "") for a in authors) if authors else None
                match = AudibleMatch(
                    asin=product_asin,
                    title=product.get("title"),
                    subtitle=product.get("subtitle"),
                    author=author_str,
                )
                if verbose:
                    ui.verbose(f"[AUDNEXUS] Using catalog data for {product_asin}: '{match.title}'")
            else:
                cache[product_asin] = None

        if match:
            match.confidence = _score_audible_match(
                match, search_title, search_author,
                expected_book_num=first.fn_book_number,
            )
            candidates.append(match)

    if not candidates:
        if extract_client:
            ai_match = _ai_identify_book(book, extract_client, verbose=verbose)
            if ai_match:
                cache[cache_key] = _match_to_dict(ai_match)
                return ai_match
        cache[cache_key] = None
        return None

    # AI-assisted selection
    if extract_client:
        picked_asin = _ai_pick_best_match(
            book, candidates, extract_client,
            max_candidates=max_candidates, verbose=verbose,
        )
        if picked_asin:
            best = next((c for c in candidates if c.asin and c.asin.upper() == picked_asin), None)
            if best:
                best.confidence = 1.0
                if verbose:
                    ui.verbose(f"[AI] Selected: '{best.title}' by {best.author} "
                              f"(series={best.series} #{best.sequence}) ASIN={best.asin}")
                cache[cache_key] = _match_to_dict(best)
                return best
        if verbose:
            ui.verbose("[AI] No match selected by AI, marking as unmatched")
        cache[cache_key] = None
        return None

    # Fallback: fuzzy scoring
    candidates.sort(key=lambda m: m.confidence, reverse=True)
    best = candidates[0]

    if verbose:
        ui.verbose(f"[AUDIBLE] Best match: '{best.title}' by {best.author} "
                   f"(series={best.series} #{best.sequence}) confidence={best.confidence:.2f}")
        for c in candidates[1:]:
            ui.verbose(f"[AUDIBLE]   also: '{c.title}' by {c.author} confidence={c.confidence:.2f}")

    min_confidence = 0.5
    if best.confidence < min_confidence:
        if verbose:
            ui.verbose(f"[AUDIBLE] Best match confidence {best.confidence:.2f} below threshold {min_confidence}, discarding")
        cache[cache_key] = None
        return None

    cache[cache_key] = _match_to_dict(best)
    return best


# ---------------------------------------------------------------------------
# Phase runner
# ---------------------------------------------------------------------------

def run_identification(books: list, cfg: Config, extract_client=None):
    """Phase 3: Look up each book on Audible and attach the best match."""
    from quackaloger.ui import ui

    if _requests is None:
        ui.warn("'requests' not installed -- skipping Audible lookups.  pip install requests")
        return

    verbose = cfg.verbosity in ("verbose", "debug")
    ai_label = " + AI matching" if extract_client else " (fuzzy matching)"
    ui.phase(3, f"Audible lookup for {len(books)} books{ai_label}")

    cache = load_cache(cfg.tool_dir)
    matched = 0
    skipped = 0
    failed = 0

    ui.flavor("api_waiting")
    progress = ui.progress(len(books), desc="Identifying", unit="books")
    with progress:
        task = progress.add_task("Identifying", total=len(books))
        for i, book in enumerate(books, 1):
            if verbose:
                ui.verbose(f"[{i}/{len(books)}] Looking up: {book.source_dir}")

            try:
                match = lookup_book(book, cache, cfg, extract_client=extract_client)
                if match:
                    book.audible_match = match
                    book.asin = match.asin
                    matched += 1
                else:
                    skipped += 1
            except Exception as e:
                if verbose:
                    ui.error(f"Audible lookup failed: {e}")
                failed += 1

            progress.advance(task)

    save_cache(cfg.tool_dir, cache)
    ui.info(f"Audible results: {matched} matched, {skipped} no match, {failed} errors")
