"""
Ohio parcel fetcher — three counties, one schema.
All Ohio counties use the state-mandated LGIM (Local Government Information Model)
with identical field names. Ohio DTE property class codes:
  5xx = Commercial / Industrial  (510-549 = Commercial, 550-599 = Industrial)
  490 = Vacant Commercial-Industrial land
  4xx = Other vacant / agricultural
  1xx = Residential (excluded)

Counties:
  Franklin  (Columbus)   — gis.franklincountyohio.gov  SP Ohio South (102723) → outSR=4326
  Cuyahoga  (Cleveland)  — gis.cuyahogacounty.us        already WGS84
  Hamilton  (Cincinnati) — services.arcgis.com (CAGIS)  SP Ohio South (102723) → outSR=4326

Ring centroid used for all (returnCentroid not supported).
Owner mailing: PSTLCITYSTZIP = "COLUMBUS OH 43215" → parsed for city/state/zip.
"""

import json
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request

import pandas as pd

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
_COUNTIES = {
    "franklin": {
        "url":   "https://gis.franklincountyohio.gov/hosting/rest/services/ParcelFeatures/Parcel_Features/MapServer/0/query",
        "name":  "Franklin County",
        "wgs84": False,
    },
    # Cuyahoga and Hamilton endpoints need live verification — coming soon
}

_HEADERS    = {"User-Agent": "ParcelFinderBot/1.0 (internal drone-hub research tool)"}
_PAGE_SIZE  = 2000
_MAX_RETRY  = 5
_RETRY_CODES= {429, 500, 503}

_OUT_FIELDS = ",".join([
    "PARCELID",
    "STATEDAREA",       # legal acres (some counties use ACRES)
    "CLASSCD",          # Ohio DTE property class code
    "CLASSDSCRP",       # description of class
    "SITEADDRESS",
    "ZIPCD",
    "OWNERNME1",
    "OWNERNME2",
    "MAILNME1",         # owner mailing address line 1
    "MAILNME2",         # owner mailing address line 2
    "PSTLCITYSTZIP",    # "COLUMBUS OH 43215"
    "LNDVALUEBASE",
    "BLDVALUEBASE",
    "TOTVALUEBASE",
    "BLDGAREA",         # building area sqft (0 or null = unimproved)
])

# "CITY ST ZIP" or "CITY ST ZIP-EXT"
_ADDR_RE = re.compile(r"^(.*?)\s+([A-Z]{2})\s+(\d{5}(?:-\d{4})?)$")

_GOV_TERMS = (
    "CITY OF ", "COUNTY OF ", "STATE OF ", "UNITED STATES",
    "DEPT OF ", "DEPARTMENT OF ", "METRO PARK", "SCHOOL DIST",
    "METROPOLITAN", "TRANSIT", "PORT OF ", " ISD",
    "OHIO DOT", "ODOT",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ring_centroid(geometry):
    rings = (geometry or {}).get("rings", [])
    if not rings or not rings[0]:
        return None, None
    pts   = rings[0]
    lats  = [p[1] for p in pts if len(p) >= 2]
    lngs  = [p[0] for p in pts if len(p) >= 2]
    if not lats:
        return None, None
    return sum(lats) / len(lats), sum(lngs) / len(lngs)


def _get_query(url, params):
    qs  = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{url}?{qs}", headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.loads(r.read())


def _fetch_page(url, params):
    for attempt in range(1, _MAX_RETRY + 1):
        try:
            data = _get_query(url, params)
        except urllib.error.HTTPError as e:
            if e.code in _RETRY_CODES and attempt < _MAX_RETRY:
                time.sleep(2 ** attempt + random.uniform(0, 1))
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            if attempt < _MAX_RETRY:
                time.sleep(2 ** attempt + random.uniform(0, 1))
                continue
            raise
        if "error" in data:
            msg  = data["error"].get("message", str(data["error"]))
            code = data["error"].get("code", 0)
            if ("too many requests" in msg.lower() or code in _RETRY_CODES) and attempt < _MAX_RETRY:
                time.sleep(2 ** attempt + random.uniform(0, 1))
                continue
            raise ValueError(f"Ohio GIS API error: {msg}")
        return data
    raise ValueError(f"Ohio GIS API throttled after {_MAX_RETRY} attempts")


def _parse_postal(raw):
    m = _ADDR_RE.match((raw or "").strip())
    return (m.group(1).strip(), m.group(2), m.group(3)) if m else ("", "", "")


def _classify(classcd):
    """Map Ohio DTE class code to Commercial / Industrial / Vacant."""
    c = (classcd or "").strip()
    if c == "490":
        return "Vacant"
    if c.startswith("5"):
        n = int(c) if c.isdigit() else 0
        if 550 <= n <= 599:
            return "Industrial"
        return "Commercial"
    return None   # skip residential, agricultural, exempt, etc.


# ---------------------------------------------------------------------------
# Core paginating fetcher
# ---------------------------------------------------------------------------

def _fetch_county(county_key, max_value, min_acres, max_acres, property_classes):
    cfg  = _COUNTIES[county_key]
    url  = cfg["url"]
    wgs  = cfg["wgs84"]
    name = cfg["name"]

    # Build WHERE
    classes = property_classes or ["Commercial", "Industrial", "Vacant"]
    parts = []
    if "Commercial" in classes:
        parts.append(
            "(CLASSCD >= '510' AND CLASSCD <= '549')"
        )
    if "Industrial" in classes:
        parts.append(
            "(CLASSCD >= '550' AND CLASSCD <= '599')"
        )
    if "Vacant" in classes:
        parts.append("CLASSCD = '490'")
    if not parts:
        return []

    class_expr = " OR ".join(parts)
    size_field = "STATEDAREA"   # legal acres — works on all 3 counties
    where = (
        f"({class_expr})"
        f" AND {size_field} >= {min_acres} AND {size_field} <= {max_acres}"
        f" AND TOTVALUEBASE > 0 AND TOTVALUEBASE <= {max_value}"
    )

    rows   = []
    offset = 0
    while True:
        params = {
            "where":             where,
            "outFields":         _OUT_FIELDS,
            "returnGeometry":    "true",
            "resultOffset":      offset,
            "resultRecordCount": _PAGE_SIZE,
            "orderByFields":     "OBJECTID",
            "f":                 "json",
        }
        if not wgs:
            params["outSR"] = "4326"

        data  = _fetch_page(url, params)
        batch = data.get("features", [])

        for feat in batch:
            a   = feat.get("attributes", {})
            lat, lng = _ring_centroid(feat.get("geometry"))
            if lat is None:
                continue

            owner = str(a.get("OWNERNME1") or "").strip()
            if any(t in owner.upper() for t in _GOV_TERMS):
                continue

            classcd = str(a.get("CLASSCD") or "").strip()
            pc      = _classify(classcd)
            if pc not in classes:
                continue

            acres   = float(a.get("STATEDAREA") or 0)
            postal  = str(a.get("PSTLCITYSTZIP") or "").strip()
            owner_city, owner_state, owner_zip = _parse_postal(postal)

            rows.append({
                "parcel_id":      str(a.get("PARCELID") or "").strip(),
                "address":        str(a.get("SITEADDRESS") or "").strip(),
                "city":           "",
                "zip":            str(a.get("ZIPCD") or "").strip(),
                "property_class": pc,
                "land_sqft":      round(acres * 43560, 1),
                "land_acres":     round(acres, 4),
                "assessed_value": float(a.get("TOTVALUEBASE") or 0),
                "owner_name":     owner,
                "owner_address":  str(a.get("MAILNME1") or "").strip(),
                "owner_city":     owner_city,
                "owner_state":    owner_state,
                "owner_zip":      owner_zip,
                "lat":            round(lat, 6),
                "lng":            round(lng, 6),
                "out_of_state":   owner_state not in ("OH", ""),
                "county":         name,
                "luc_msg":        str(a.get("CLASSDSCRP") or classcd).strip(),
            })

        if not data.get("exceededTransferLimit", False):
            break
        offset += len(batch)
        time.sleep(0.3)

    return rows


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_parcels(city_cfg, property_classes, max_value, min_acres, max_acres):
    counties = city_cfg.get("counties", ["franklin"])
    all_rows = []

    for county_key in counties:
        if county_key not in _COUNTIES:
            print(f"[ohio] Skipping {county_key} — endpoint not yet verified")
            continue
        try:
            print(f"[ohio] fetching {_COUNTIES[county_key]['name']}...")
            rows = _fetch_county(county_key, max_value, min_acres, max_acres, property_classes)
            print(f"[ohio]   → {len(rows)} parcels")
            all_rows.extend(rows)
        except Exception as e:
            print(f"[ohio] WARNING: {_COUNTIES[county_key]['name']} failed: {e}")
        time.sleep(0.4)

    empty_cols = [
        "parcel_id", "address", "city", "zip", "property_class",
        "land_sqft", "land_acres", "assessed_value",
        "owner_name", "owner_address", "owner_city", "owner_state", "owner_zip",
        "lat", "lng", "out_of_state", "county", "luc_msg",
    ]
    if not all_rows:
        print("[ohio] WARNING: 0 parcels — run probe to verify CLASSCD values")
        return pd.DataFrame(columns=empty_cols)

    df = pd.DataFrame(all_rows)
    df = df.drop_duplicates(subset="parcel_id", keep="first")
    return df.dropna(subset=["lat", "lng"]).reset_index(drop=True)
