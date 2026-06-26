"""
North Carolina parcel fetcher.
  Charlotte → Mecklenburg County: PLN/VacantLand/MapServer/0  (undeveloped parcels only)
  Raleigh   → Wake County:        Property/Parcels/FeatureServer/0

Mecklenburg quirks:
  - VacantLand layer = all undeveloped parcels (already filtered at source)
  - landusecode C* = Commercial-zoned vacant, I* = Industrial-zoned vacant, else = Vacant
  - city/state/zipcode = owner mailing city/state/zip; municipality = property city
  - FULL_ADDRESS field available for situs address

Wake County quirks:
  - LAND_CODE: C = Commercial, I = Industrial; BLDG_VAL = 0 catches vacant/unimproved
  - ADDR2 typically "CITY STATE ZIP" — parsed for owner_city/state/zip
  - ZIPNUM = property zip; CITY_DECODE = property city name

Coords: outSR=4326 on both — WGS84 returned directly. Ring centroid used.
GET queries, offset pagination, 5-attempt exponential backoff.
"""

import json
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request

import pandas as pd

_MECK_URL = (
    "https://gis.charlottenc.gov/arcgis/rest/services"
    "/PLN/VacantLand/MapServer/0/query"
)
_WAKE_URL = (
    "https://maps.wakegov.com/arcgis/rest/services"
    "/Property/Parcels/FeatureServer/0/query"
)

_HEADERS = {"User-Agent": "ParcelFinderBot/1.0 (internal drone-hub research tool)"}
_PAGE_SIZE = 2000
_RETRY_CODES = {429, 503, 500}
_MAX_ATTEMPTS = 5

_MECK_FIELDS = ",".join([
    "taxpid", "nc_pin", "totalac",
    "ownerlastname", "ownerfirstname",
    "FULL_ADDRESS", "municipality",
    "mailaddr1", "city", "state", "zipcode",
    "landvalue", "totalvalue", "landusecode", "descpropertyuse",
])

_WAKE_FIELDS = ",".join([
    "PIN_NUM", "OWNER", "ADDR1", "ADDR2",
    "SITE_ADDRESS", "CITY_DECODE", "ZIPNUM",
    "DEED_ACRES", "BLDG_VAL", "LAND_VAL", "TOTAL_VALUE_ASSD",
    "TYPE_AND_USE", "TYPE_USE_DECODE", "LAND_CODE",
])

# "CITY STATE ZIP[-EXT]" — same format as Georgia OwnerAddr2
_ADDR_RE = re.compile(r"^(.*?)\s+([A-Z]{2})\s+(\d{5}(?:-\d{4})?)$")


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


def _get_query(url, params):
    qs = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{url}?{qs}", headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _fetch_page(url, params):
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            data = _get_query(url, params)
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
            raise ValueError(f"NC API error: {msg}")
        return data
    raise ValueError(f"NC API throttled after {_MAX_ATTEMPTS} attempts")


def _paginate(url, where, out_fields):
    features, offset = [], 0
    while True:
        params = {
            "where":             where,
            "outFields":         out_fields,
            "returnGeometry":    "true",
            "outSR":             "4326",
            "resultOffset":      offset,
            "resultRecordCount": _PAGE_SIZE,
            "orderByFields":     "OBJECTID",
            "f":                 "json",
        }
        data = _fetch_page(url, params)
        batch = data.get("features", [])
        features.extend(batch)
        if not data.get("exceededTransferLimit", False):
            break
        offset += len(batch)
        time.sleep(0.3)
    return features


def _parse_addr(raw):
    m = _ADDR_RE.match((raw or "").strip())
    return (m.group(1).strip(), m.group(2), m.group(3)) if m else ("", "", "")


# ── Mecklenburg (Charlotte) ────────────────────────────────────────────────

def _meck_normalize(features, prop_class):
    rows = []
    for feat in features:
        a   = feat.get("attributes", {})
        lat, lng = _ring_centroid(feat.get("geometry"))
        if lat is None:
            continue

        acres = float(a.get("totalac") or 0)
        owner_state = str(a.get("state") or "").strip().upper()

        rows.append({
            "parcel_id":      str(a.get("taxpid") or a.get("nc_pin") or "").strip(),
            "address":        str(a.get("FULL_ADDRESS") or "").strip(),
            "city":           str(a.get("municipality") or "").strip().title(),
            "zip":            "",
            "property_class": prop_class,
            "land_sqft":      round(acres * 43560, 1),
            "land_acres":     round(acres, 4),
            "assessed_value": float(a.get("totalvalue") or 0),
            "owner_name":     f"{a.get('ownerfirstname','') or ''} {a.get('ownerlastname','') or ''}".strip(),
            "owner_address":  str(a.get("mailaddr1") or "").strip(),
            "owner_city":     str(a.get("city") or "").strip(),
            "owner_state":    owner_state,
            "owner_zip":      str(a.get("zipcode") or "").strip(),
            "lat":            round(lat, 6),
            "lng":            round(lng, 6),
            "out_of_state":   owner_state not in ("NC", ""),
            "county":         "Mecklenburg County",
            "luc_msg":        str(a.get("descpropertyuse") or a.get("landusecode") or "").strip(),
        })
    return rows


def _fetch_charlotte(max_value, min_acres, max_acres):
    base = (
        f"totalac >= {min_acres} AND totalac <= {max_acres}"
        f" AND totalvalue > 0 AND totalvalue <= {max_value}"
    )
    queries = [
        ("Commercial", f"landusecode LIKE 'C%' AND {base}"),
        ("Industrial", f"landusecode LIKE 'I%' AND {base}"),
        ("Vacant",     f"landusecode NOT LIKE 'C%' AND landusecode NOT LIKE 'I%' AND landusecode NOT LIKE 'R%' AND landusecode NOT LIKE 'UR%' AND {base}"),
    ]
    rows = []
    for prop_class, where in queries:
        feats = _paginate(_MECK_URL, where, _MECK_FIELDS)
        rows.extend(_meck_normalize(feats, prop_class))
        time.sleep(0.3)
    return rows


# ── Wake County (Raleigh) ─────────────────────────────────────────────────

def _wake_normalize(features, prop_class):
    rows = []
    for feat in features:
        a   = feat.get("attributes", {})
        lat, lng = _ring_centroid(feat.get("geometry"))
        if lat is None:
            continue

        acres = float(a.get("DEED_ACRES") or 0)
        owner_city, owner_state, owner_zip = _parse_addr(a.get("ADDR2"))

        rows.append({
            "parcel_id":      str(a.get("PIN_NUM") or "").strip(),
            "address":        str(a.get("SITE_ADDRESS") or "").strip(),
            "city":           str(a.get("CITY_DECODE") or "").strip().title(),
            "zip":            str(a.get("ZIPNUM") or "").strip(),
            "property_class": prop_class,
            "land_sqft":      round(acres * 43560, 1),
            "land_acres":     round(acres, 4),
            "assessed_value": float(a.get("TOTAL_VALUE_ASSD") or 0),
            "owner_name":     str(a.get("OWNER") or "").strip(),
            "owner_address":  str(a.get("ADDR1") or "").strip(),
            "owner_city":     owner_city,
            "owner_state":    owner_state,
            "owner_zip":      owner_zip,
            "lat":            round(lat, 6),
            "lng":            round(lng, 6),
            "out_of_state":   owner_state not in ("NC", ""),
            "county":         "Wake County",
            "luc_msg":        str(a.get("TYPE_USE_DECODE") or a.get("TYPE_AND_USE") or "").strip(),
        })
    return rows


def _fetch_raleigh(max_value, min_acres, max_acres):
    base = (
        f"DEED_ACRES >= {min_acres} AND DEED_ACRES <= {max_acres}"
        f" AND TOTAL_VALUE_ASSD > 0 AND TOTAL_VALUE_ASSD <= {max_value}"
    )
    queries = [
        ("Commercial", f"LAND_CODE = 'C' AND {base}"),
        ("Industrial", f"LAND_CODE = 'I' AND {base}"),
        ("Vacant",     f"BLDG_VAL = 0 AND LAND_CODE NOT IN ('R','P','A') AND {base}"),
    ]
    rows = []
    for prop_class, where in queries:
        feats = _paginate(_WAKE_URL, where, _WAKE_FIELDS)
        rows.extend(_wake_normalize(feats, prop_class))
        time.sleep(0.3)
    return rows


# ── Public API ────────────────────────────────────────────────────────────

def fetch_nc_parcels(
    city:      str   = "charlotte",
    max_value: int   = 500_000,
    min_acres: float = 0.10,
    max_acres: float = 0.50,
) -> pd.DataFrame:
    all_rows = []
    c = city.lower()
    if c in ("charlotte", "all"):
        all_rows.extend(_fetch_charlotte(max_value, min_acres, max_acres))
    if c in ("raleigh", "raleigh-durham", "all"):
        all_rows.extend(_fetch_raleigh(max_value, min_acres, max_acres))

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


def fetch_parcels(city_cfg, property_classes, max_value, min_acres, max_acres):
    return fetch_nc_parcels(
        city=city_cfg.get("city", "charlotte"),
        max_value=max_value,
        min_acres=min_acres,
        max_acres=max_acres,
    )
