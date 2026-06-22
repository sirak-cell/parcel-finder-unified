"""
Top-level router for the unified Parcel Finder.

Public API:
  fetch_parcels(state, city_name, property_classes, max_value, min_acres, max_acres)
      -> pd.DataFrame (normalized schema)

  fetch_walmarts(city_cfg) -> list[dict]  # {name, lat, lng}
"""

import json
import urllib.parse
import urllib.request

from config import MARKETS

import fetchers.utah       as _utah
import fetchers.new_mexico as _nm
import fetchers.colorado   as _co
import fetchers.florida    as _fl
import fetchers.arizona    as _az

_FETCHER_MAP = {
    "utah":       _utah,
    "new_mexico": _nm,
    "colorado":   _co,
    "florida":    _fl,
    "arizona":    _az,
}

_HEADERS = {"User-Agent": "ParcelFinderBot/1.0 (internal drone-hub research tool)"}
_OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
]


def fetch_parcels(state, city_name, property_classes, max_value, min_acres, max_acres):
    state_cfg  = MARKETS[state]
    city_cfg   = state_cfg["cities"][city_name]
    fetcher_id = state_cfg["fetcher"]
    module     = _FETCHER_MAP[fetcher_id]
    return module.fetch_parcels(city_cfg, property_classes, max_value, min_acres, max_acres)


def fetch_walmarts(city_cfg):
    bbox   = city_cfg.get("overpass_bbox", "")
    static = city_cfg.get("walmart_static", [])

    if not bbox:
        return list(static)

    query = f"""
[out:json][timeout:60];
(
  nwr["brand:wikidata"="Q483551"]["name"~"Supercenter"]({bbox});
  nwr["brand"="Walmart"]["name"~"Supercenter",i]({bbox});
);
out center tags;
"""
    payload = urllib.parse.urlencode({"data": query}).encode()

    for url in _OVERPASS_URLS:
        try:
            req = urllib.request.Request(
                url, data=payload,
                headers={**_HEADERS, "Content-Type": "application/x-www-form-urlencoded"},
            )
            with urllib.request.urlopen(req, timeout=60) as r:
                elements = json.loads(r.read()).get("elements", [])
            walmarts = []
            seen = set()
            for e in elements:
                lat = e.get("lat") or (e.get("center") or {}).get("lat")
                lng = e.get("lon") or (e.get("center") or {}).get("lon")
                if lat and lng:
                    k = (round(lat, 4), round(lng, 4))
                    if k not in seen:
                        seen.add(k)
                        walmarts.append({
                            "name": e.get("tags", {}).get("name", "Walmart Supercenter"),
                            "lat": float(lat),
                            "lng": float(lng),
                        })
            if walmarts:
                return walmarts
        except Exception:
            continue

    return list(static)
