"""
Find software companies in any city, from open data that allows automated use:

- OpenStreetMap: IT / software offices mapped in the city (place search via Nominatim, offices via Overpass).
  Usage policies: identify the app, at most ~1 request/second, cache results. Data (c) OpenStreetMap contributors, ODbL.
- Wikidata: software / IT companies headquartered in the city (public SPARQL endpoint, CC0).
- Y Combinator's company list (already cached by companies.py): hiring startups that list the city.

Google Maps is deliberately not used: its terms forbid scraping, and the Places API needs a billing account.
Results are cached for a day per city. Nothing here visits company websites; adding a company to the
watchlist hands it to companies.py, which finds its careers board politely (robots.txt respected).
"""

from concurrent.futures import ThreadPoolExecutor
import json
import logging
from pathlib import Path
import re
import time
from typing import Any, Dict, List, Optional

import requests

from src.core.db import list_companies
from src.opportunities.companies import _norm, _wait_turn, _yc_companies

logger = logging.getLogger("sarthi.discover")

ROOT = Path(__file__).resolve().parent.parent.parent
CACHE_DIR = ROOT / "data" / "city_companies"
CACHE_SECONDS = 86400
HEADERS = {"User-Agent": "SARTHI/1.0 (personal job search assistant; https://github.com/kalp-cg/Ai-Job-Finder)"}
TIMEOUT = 60

NOMINATIM = "https://nominatim.openstreetmap.org/search"
# The main Overpass server is sometimes overloaded; these public instances serve the same data.
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.private.coffee/api/interpreter",
            "https://overpass.kumi.systems/api/interpreter"]
WIKIDATA_SEARCH = "https://www.wikidata.org/w/api.php"
WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"

# Wikidata industries: software, IT, IT services, IT consulting, video games, SaaS, internet, e-commerce, fintech.
INDUSTRIES = "wd:Q638608 wd:Q11661 wd:Q880371 wd:Q1662546 wd:Q7397 wd:Q21129801 wd:Q2269947 wd:Q1047113 wd:Q11016"
# office=company covers every kind of company; keep the ones whose name says software / tech.
TECH_NAME = re.compile(r"tech|software|\bsoft\b|soft[a-z]*\b|infotech|digital|\bai\b|data|cloud|labs?\b|\bweb\b|\bapps?\b|"
                       r"cyber|\bit\b|systems|computer|infosys|informatics|bytes?\b|code|dev\b", re.I)
# Shops, service counters and training centres get mapped as offices too; they aren't employers to apply to.
NOT_EMPLOYER = re.compile(r"\b(store|shop|showroom|outlet|repair|service cent(er|re)|gallery|classes|coaching|institute|"
                          r"training|academy|tuition|cctv|xerox|cyber cafe|recharge|solar)\b", re.I)
# Spellings the YC list uses for the same city.
ALIASES = {"bengaluru": ["bangalore"], "bangalore": ["bengaluru"], "gurugram": ["gurgaon"], "gurgaon": ["gurugram"],
           "mumbai": ["bombay"], "delhi": ["new delhi"], "new delhi": ["delhi"], "gift city": ["gandhinagar"]}
# A whole state or country would make OpenStreetMap queries enormous; search its centre instead.
MAX_SPAN_DEG = 0.6
# A small district (e.g. GIFT City) often has few mapped offices; then search about 15 km around it too.
FEW_RESULTS = 5
WIDEN_HALF_DEG = 0.14


def _get(url: str, params: Dict[str, Any], accept: str = "application/json") -> requests.Response:
    _wait_turn(url, gap=1.1)
    return requests.get(url, params=params, headers={**HEADERS, "Accept": accept}, timeout=TIMEOUT)


def _site(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    url = url.strip().split(";")[0]
    if not re.match(r"https?://", url):
        url = "https://" + url
    return url if re.match(r"https?://[^/\s]+\.[a-z]{2,}", url, re.I) else None


# ---------------------------------------------------------------- the place

def resolve_place(city: str) -> Dict[str, Any]:
    """Nominatim: display name, bounding box (south, west, north, east) and Wikidata id of the place."""
    r = _get(NOMINATIM, {"q": city, "format": "json", "limit": 1, "extratags": 1, "accept-language": "en"})
    r.raise_for_status()
    hits = r.json()
    if not hits:
        raise LookupError(f"couldn't find a place called \"{city}\" on the map")
    hit = hits[0]
    south, north, west, east = (float(x) for x in hit["boundingbox"])
    if north - south > MAX_SPAN_DEG or east - west > MAX_SPAN_DEG:
        lat, lon = float(hit["lat"]), float(hit["lon"])
        half = MAX_SPAN_DEG / 2
        south, north, west, east = lat - half, lat + half, lon - half, lon + half
    lat, lon = float(hit["lat"]), float(hit["lon"])
    return {
        "center": (lat, lon),
        "name": hit.get("name") or city,
        "display_name": hit.get("display_name", city),
        "bbox": (south, west, north, east),
        "wikidata": (hit.get("extratags") or {}).get("wikidata"),
    }


# ---------------------------------------------------------------- sources

def from_openstreetmap(place: Dict[str, Any]) -> List[Dict[str, Any]]:
    s, w, n, e = place["bbox"]
    box = f"({s:.4f},{w:.4f},{n:.4f},{e:.4f})"
    query = (f'[out:json][timeout:50];(nwr["office"~"^(it|software|telecommunication)$"]["name"]{box};'
             f'nwr["office"="company"]["name"]{box};);out tags center 3000;')
    elements = None
    # Free servers are often briefly busy: try each, then the main one once more after a pause.
    for attempt, server in enumerate(OVERPASS + OVERPASS[:1]):
        if attempt == len(OVERPASS):
            time.sleep(4)
        try:
            r = _get(server, {"data": query})
            r.raise_for_status()
            elements = r.json()["elements"]
            break
        except (requests.RequestException, ValueError, KeyError) as err:
            logger.info(f"Overpass {server} failed: {err}")
    if elements is None:
        raise RuntimeError("OpenStreetMap's servers are busy right now. Try again in a few minutes.")
    out = []
    for el in elements:
        tags = el.get("tags") or {}
        name = (tags.get("name:en") or tags.get("name") or "").strip()
        if not name:
            continue
        if tags.get("office") == "company" and not TECH_NAME.search(f"{name} {tags.get('description', '')}"):
            continue
        if NOT_EMPLOYER.search(name) or tags.get("shop"):
            continue
        out.append({"name": name, "website": _site(tags.get("website") or tags.get("contact:website") or tags.get("url")),
                    "source": "OpenStreetMap"})
    # Buildings in a tech park are often mapped as offices named after the building ("Colorado", "Congo"),
    # all linking to the park's site. A website shared by 3+ differently named places is a landlord, not an employer.
    names_per_host: Dict[str, set] = {}
    for row in out:
        if row["website"]:
            host = re.sub(r"^www\.", "", row["website"].split("/")[2].lower())
            names_per_host.setdefault(host, set()).add(_norm(row["name"]))
    landlords = {h for h, names in names_per_host.items() if len(names) >= 3}
    return [r for r in out if not r["website"] or re.sub(r"^www\.", "", r["website"].split("/")[2].lower()) not in landlords]


def from_wikidata(place: Dict[str, Any]) -> List[Dict[str, Any]]:
    qid = place.get("wikidata")
    if not qid:
        r = _get(WIKIDATA_SEARCH, {"action": "wbsearchentities", "search": place["name"], "language": "en",
                                   "type": "item", "limit": 1, "format": "json"})
        r.raise_for_status()
        hits = r.json().get("search") or []
        if not hits:
            return []
        qid = hits[0]["id"]
    query = f"""SELECT DISTINCT ?c ?cLabel ?site WHERE {{
      {{ ?c wdt:P159 wd:{qid} }} UNION {{ ?c wdt:P159 ?hq . ?hq wdt:P131 wd:{qid} }}
      ?c wdt:P452 ?industry . VALUES ?industry {{ {INDUSTRIES} }}
      FILTER NOT EXISTS {{ ?c wdt:P576 ?dissolved }}
      OPTIONAL {{ ?c wdt:P856 ?site }}
      SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
    }} LIMIT 500"""
    r = _get(WIKIDATA_SPARQL, {"query": query}, accept="application/sparql-results+json")
    r.raise_for_status()
    out = []
    for row in r.json()["results"]["bindings"]:
        name = row["cLabel"]["value"]
        if re.fullmatch(r"Q\d+", name):  # no English label
            continue
        out.append({"name": name, "website": _site((row.get("site") or {}).get("value")), "source": "Wikidata"})
    return out


def from_yc(place: Dict[str, Any], city: str) -> List[Dict[str, Any]]:
    names = {city.lower().strip(), place["name"].lower()}
    for n in list(names):
        names.update(ALIASES.get(n, []))
    rx = re.compile(r"\b(" + "|".join(re.escape(n) for n in names if n) + r")\b", re.I)
    return [{"name": c["name"], "website": _site(c.get("website")), "source": "Y Combinator"}
            for c in _yc_companies()
            if c.get("isHiring") and c.get("status") in ("Active", "Public") and rx.search(c.get("all_locations") or "")]


# ---------------------------------------------------------------- putting it together

def _cache_path(city: str) -> Path:
    return CACHE_DIR / (re.sub(r"[^a-z0-9]+", "-", city.lower()).strip("-") + ".json")


def find_companies(city: str, refresh: bool = False) -> Dict[str, Any]:
    """
    {"city", "place", "companies": [{"name", "website", "sources", "watching"}], "counts", "errors"}.
    A source that fails is reported in errors; the others still return.
    """
    city = city.strip()
    if not city:
        raise ValueError("Type a city")
    path = _cache_path(city)
    if not refresh and path.exists() and time.time() - path.stat().st_mtime < CACHE_SECONDS:
        result = json.loads(path.read_text(encoding="utf-8"))
    else:
        place = resolve_place(city)
        jobs = {"OpenStreetMap": lambda: from_openstreetmap(place), "Wikidata": lambda: from_wikidata(place),
                "Y Combinator": lambda: from_yc(place, city)}
        found: Dict[str, List[Dict[str, Any]]] = {}
        errors: Dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {name: pool.submit(fn) for name, fn in jobs.items()}
            for name, fut in futures.items():
                try:
                    found[name] = fut.result()
                except Exception as err:
                    from src.opportunities.sources import plain_error
                    errors[name] = plain_error(err) if isinstance(err, requests.RequestException) else str(err)[:200]
                    found[name] = []
        note = None
        if sum(len(rows) for rows in found.values()) < FEW_RESULTS and "OpenStreetMap" not in errors:
            lat, lon = place["center"]
            wider = {**place, "bbox": (lat - WIDEN_HALF_DEG, lon - WIDEN_HALF_DEG, lat + WIDEN_HALF_DEG, lon + WIDEN_HALF_DEG)}
            try:
                found["OpenStreetMap"] = from_openstreetmap(wider)
                note = f"Few companies are mapped in {place['name']} itself, so this includes about 15 km around it."
            except Exception as err:
                errors["OpenStreetMap"] = str(err)[:200]
        merged: Dict[str, Dict[str, Any]] = {}
        for rows in found.values():
            for row in rows:
                key = _norm(row["name"]) or row["name"].lower()
                entry = merged.setdefault(key, {"name": row["name"], "website": None, "sources": []})
                entry["website"] = entry["website"] or row["website"]
                if row["source"] not in entry["sources"]:
                    entry["sources"].append(row["source"])
        result = {
            "city": city,
            "place": place["display_name"],
            "companies": sorted(merged.values(), key=lambda c: (-len(c["sources"]), not c["website"], c["name"].lower())),
            "counts": {name: len(rows) for name, rows in found.items()},
            "errors": errors,
            "note": note,
        }
        if not errors:  # don't keep a half result for a day
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(result), encoding="utf-8")
    watched = {_norm(c["name"]) for c in list_companies() if c["discovery_state"] != "duplicate"}
    for c in result["companies"]:
        c["watching"] = _norm(c["name"]) in watched
    return result
