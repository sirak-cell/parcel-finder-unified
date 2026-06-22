"""
Georgia parcel fetcher — Fulton County (Atlanta metro).
Source: Fulton County GIS PropertyMapViewer MapServer/11

Quirks:
  - No city/zip field in layer — city left empty, zip parsed from Address when possible
  - OwnerAddr2 typically "CITY ST ZIP" — parsed for owner_city/state/zip
  - Commercial (LUCode C*) and Industrial (LUCode I*) queried separately
  - Vacant = ImprAppr=0; C*/I* parcels with ImprAppr=0 deduplicated — C/I wins
  - Ring centroid (returnCentroid not supported on this server)
  - GET queries with offset pagination, 5-attempt exponential backoff
"""

import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import json

import pandas as pd

_ENDPOINT = (
    "https://gismaps.fultoncountyga.gov/arcgispub2/rest/services"
    "/PropertyMapViewer/PropertyMapViewer/MapServer/11/query"
)
_HEADERS = {"User-Agent": "ParcelFinderBot/1.0 (internal drone-hub research tool)"}
_PAGE_SIZE = 2000
_RETRY_CODES = {429, 503, 500}
_MAX_ATTEMPTS = 5

_OUT_FIELDS = ",".join([
    "ParcelID", "Address", "Owner",
    "OwnerAddr1", "OwnerAddr2",
    "LUCode", "LandAcres", "TotAppr", "ImprAppr",
])

# Pattern: "CITY STATE ZIP" or "CITY STATE ZIPEXT"
_OWNER_ADDR2_RE = re.compile(
    r"^(.*?)\s+([A-Z]{2})\s+(\d{5}(?:-\d{4})?)$"
)


def _ring_centroid(geometry):
    rings = (geometry or {}).get("rings", [])
    if not rings or not rings[0]:
        return None, None
    pts = rings[0]
    lats = [p[1] for p in pts if len(p) >= 2]
    lngs = [p[0] for p in pts if len(p) >= 2]
    if not lats:
        return None, None
    return sum(lats) / len(lats), sum(lngs) / len(lngs)


def _get_query(params):
    qs = urllib.parse.urlencode(params)
    url = f"{_ENDPOINT}?{qs}"
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _fetch_page(params):
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            data = _get_query(params)
        except urllib.error.HTTPError as e:
            if e.code in _RETRY_CODES and attempt < _MAX_ATTEMPTS:
                time.sleep(2 ** attempt + random.uniform(0, 1))
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            if attempt < _MAX_ATTEMPTS:
                time.sleep(2 ** attempt + random.uniform(0, 1))
                continue
            raise
        if "error" in data:
            msg = data["error"].get("message", str(data["error"]))
            code = data["error"].get("code", 0)
            if ("too many requests" in msg.lower() or code in _RETRY_CODES) and attempt < _MAX_ATTEMPTS:
                time.sleep(2 ** attempt + random.uniform(0, 1))
                continue
            raise ValueError(f"Fulton County API error: {msg}")
        return data
    raise ValueError(f"Fulton County API throttled after {_MAX_ATTEMPTS} attempts")


def _parse_owner_addr2(raw):
    s = (raw or "").strip()
    m = _OWNER_ADDR2_RE.match(s)
    if m:
        return m.group(1).strip(), m.group(2), m.group(3)
    return "", "", ""


def _paginate(where_clause):
    features = []
    offset = 0
    while True:
        params = {
            "where":             where_clause,
            "outFields":         _OUT_FIELDS,
            "returnGeometry":    "true",
            "outSR":             "4326",
            "resultOffset":      offset,
            "resultRecordCount": _PAGE_SIZE,
            "orderByFields":     "ParcelID",
            "f":                 "json",
        }
        data = _fetch_page(params)
        batch = data.get("features", [])
        features.extend(batch)
        if not data.get("exceededTransferLimit", False):
            break
        offset += len(batch)
        time.sleep(0.3)
    return features


def _normalize(features, prop_class):
    rows = []
    for feat in features:
        a    = feat.get("attributes", {})
        lat, lng = _ring_centroid(feat.get("geometry"))
        if lat is None:
            continue

        acres = float(a.get("LandAcres") or 0)
        owner_city, owner_state, owner_zip = _parse_owner_addr2(a.get("OwnerAddr2"))

        rows.append({
            "parcel_id":      str(a.get("ParcelID") or "").strip(),
            "address":        str(a.get("Address") or "").strip(),
            "city":           "",
            "zip":            "",
            "property_class": prop_class,
            "land_sqft":      round(acres * 43560, 1),
            "land_acres":     round(acres, 4),
            "assessed_value": float(a.get("TotAppr") or 0),
            "owner_name":     str(a.get("Owner") or "").strip(),
            "owner_address":  str(a.get("OwnerAddr1") or "").strip(),
            "owner_city":     owner_city,
            "owner_state":    owner_state,
            "owner_zip":      owner_zip,
            "lat":            round(lat, 6),
            "lng":            round(lng, 6),
            "out_of_state":   owner_state not in ("GA", ""),
            "county":         "Fulton County",
            "luc_msg":        str(a.get("LUCode") or "").strip(),
        })
    return rows


def _build_where(max_value, min_acres, max_acres):
    return (
        f"LandAcres >= {min_acres} AND LandAcres <= {max_acres}"
        f" AND TotAppr > 0 AND TotAppr <= {max_value}"
    )


def fetch_georgia_parcels(
    county:    str   = "fulton",
    max_value: int   = 500_000,
    min_acres: float = 0.10,
    max_acres: float = 0.50,
) -> "pd.DataFrame":
    city_cfg = {"county": county}
    return fetch_parcels(city_cfg, ["Commercial", "Industrial", "Vacant"],
                         max_value, min_acres, max_acres)


def fetch_parcels(city_cfg, property_classes, max_value, min_acres, max_acres):
    if not property_classes:
        property_classes = ["Commercial", "Industrial", "Vacant"]

    base = _build_where(max_value, min_acres, max_acres)

    # Georgia Fulton County LUCode ranges:
    #   200-299 = Commercial   300-399 = Industrial
    # Vacant = ImprAppr=0 within commercial/industrial codes
    # Process Commercial and Industrial before Vacant so C/I wins dedup
    queries = []
    if "Commercial" in property_classes:
        queries.append(("Commercial", f"(LUCode >= '200' AND LUCode <= '299') AND {base}"))
    if "Industrial" in property_classes:
        queries.append(("Industrial", f"(LUCode >= '300' AND LUCode <= '399') AND {base}"))
    if "Vacant" in property_classes:
        queries.append(("Vacant",
            f"ImprAppr = 0 AND (LUCode >= '200' AND LUCode <= '399') AND {base}"))

    all_rows = []
    for prop_class, where in queries:
        feats = _paginate(where)
        all_rows.extend(_normalize(feats, prop_class))
        time.sleep(0.3)

    if not all_rows:
        return pd.DataFrame(columns=[
            "parcel_id", "address", "city", "zip", "property_class",
            "land_sqft", "land_acres", "assessed_value",
            "owner_name", "owner_address", "owner_city", "owner_state", "owner_zip",
            "lat", "lng", "out_of_state", "county", "luc_msg",
        ])

    df = pd.DataFrame(all_rows)
    # C/I wins over Vacant for same parcel_id (C/I rows come first)
    df = df.drop_duplicates(subset="parcel_id", keep="first")
    return df.dropna(subset=["lat", "lng"]).reset_index(drop=True)
