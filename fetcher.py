"""
Top-level router for the unified Parcel Finder.

Public API:
  fetch_parcels(state, city_name, property_classes, max_value, min_acres, max_acres)
      -> pd.DataFrame (normalized schema)

  fetch_walmarts(city_cfg) -> list[dict]  # {name, lat, lng}
"""

import json
import re
import urllib.parse
import urllib.request

from config import MARKETS

_HWY_RE = re.compile(r'\b(?:HWY|HIGHWAY|INTERSTATE)\b', re.I)


def _strip_highway_parcels(df):
    if df.empty or "address" not in df.columns:
        return df
    mask = df["address"].str.contains(_HWY_RE, na=False)
    return df[~mask].reset_index(drop=True)

import fetchers.utah       as _utah
import fetchers.new_mexico as _nm
import fetchers.colorado   as _co
import fetchers.florida    as _fl
import fetchers.arizona    as _az
import fetchers.georgia         as _ga
import fetchers.north_carolina  as _nc
import fetchers.ohio            as _oh
import fetchers.tennessee       as _tn

_FETCHER_MAP = {
    "utah":           _utah,
    "new_mexico":     _nm,
    "colorado":       _co,
    "florida":        _fl,
    "arizona":        _az,
    "georgia":        _ga,
    "north_carolina": _nc,
    "ohio":           _oh,
    "tennessee":      _tn,
}

_HEADERS = {"User-Agent": "ParcelFinderBot/1.0 (internal drone-hub research tool)"}
_OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
]


def fetch_parcels(state, city_name, property_classes, max_value, min_acres, max_acres):
    state_cfg = MARKETS[state]
    city_cfg  = state_cfg["cities"][city_name]

    if state == "Georgia":
        from fetchers.georgia import fetch_georgia_parcels
        df = fetch_georgia_parcels(
            county=city_cfg.get("county", "fulton"),
            max_value=max_value,
            min_acres=min_acres,
            max_acres=max_acres,
        )
    elif state == "North Carolina":
        from fetchers.north_carolina import fetch_nc_parcels
        df = fetch_nc_parcels(
            city=city_cfg.get("city", "charlotte"),
            max_value=max_value,
            min_acres=min_acres,
            max_acres=max_acres,
        )
    elif state == "Ohio":
        from fetchers.ohio import fetch_parcels as _oh_fetch
        df = _oh_fetch(city_cfg, property_classes, max_value, min_acres, max_acres)
    else:
        fetcher_id = state_cfg["fetcher"]
        module     = _FETCHER_MAP[fetcher_id]
        df = module.fetch_parcels(city_cfg, property_classes, max_value, min_acres, max_acres)

    return _strip_highway_parcels(df)


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
