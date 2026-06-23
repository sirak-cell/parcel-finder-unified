"""
Ohio parcel fetcher — multi-county, two schemas.

Franklin County uses Ohio's state-mandated LGIM schema (DTE class codes).
Cuyahoga County uses its own FO_PP_CAMA schema (property_class C/I, vacant via com_bldg_count).

Ohio DTE property class codes (LGIM / Franklin):
  510-549 = Commercial    550-599 = Industrial    490 = Vacant C/I land

Cuyahoga property_class:
  'C' = Commercial    'I' = Industrial
  Vacant = C or I parcel with com_bldg_count = 0 AND certified_tax_building = 0
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
        "url":    "https://gis.franklincountyohio.gov/hosting/rest/services/ParcelFeatures/Parcel_Features/MapServer/0/query",
        "name":   "Franklin County",
        "wgs84":  False,
        "schema": "lgim",
    },
    "cuyahoga": {
        "url":    "https://gis.cuyahogacounty.us/server/rest/services/CCFO/APPRAISAL_PARCELS_CAMA_WGS84/MapServer/0/query",
        "name":   "Cuyahoga County",
        "wgs84":  True,
        "schema": "cuyahoga",
    },
    "hamilton": {
        "url":    "https://cagisonline.hamilton-co.org/arcgis/rest/services/HCE/Cadastral/MapServer/0/query",
        "name":   "Hamilton County",
        "wgs84":  False,
        "schema": "hamilton",
    },
}

_HEADERS     = {"User-Agent": "ParcelFinderBot/1.0 (internal drone-hub research tool)"}
# CAGIS (Hamilton County) drops connections with a bot UA — needs browser-like headers
_HAM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer":    "https://cagisonline.hamilton-co.org/",
}
_PAGE_SIZE   = 2000
_MAX_RETRY   = 5
_RETRY_CODES = {429, 500, 503}

# LGIM fields (Franklin County)
_OUT_FIELDS_LGIM = ",".join([
    "PARCELID", "STATEDAREA", "CLASSCD", "CLASSDSCRP",
    "SITEADDRESS", "ZIPCD",
    "OWNERNME1", "MAILNME1", "PSTLCITYSTZIP",
    "LNDVALUEBASE", "BLDVALUEBASE", "TOTVALUEBASE", "BLDGAREA",
])

# Hamilton County (CAGIS) fields
_HAM_OUT_FIELDS = ",".join([
    "PARCELID", "CLASS", "EXLUCODE", "ACREDEED", "MKT_TOTAL_VAL", "MKTIMP",
    "ADDRNO", "ADDRST", "ADDRSF",
    "OWNNM1", "OWNNM2", "OWNAD1", "OWNADCITY", "OWNADSTATE", "OWNADZIP",
])

_HAM_EXLUCODE_COMMERCIAL = frozenset(["C", "O", "MU", "CH"])
_HAM_EXLUCODE_INDUSTRIAL = frozenset(["LI", "HI"])

# Cuyahoga-specific fields
_CUY_OUT_FIELDS = ",".join([
    "PARCELPIN",
    "parcel_acreage",
    "property_class",
    "prop_class_desc",
    "par_addr_all",       # "ADDR, CITY, STATE, ZIP"
    "parcel_zip",
    "parcel_owner",
    "mail_addr_street",
    "mail_city",
    "mail_state",
    "mail_zip",
    "certified_tax_land",
    "certified_tax_building",
    "certified_tax_total",
    "com_bldg_count",
    "tax_luc",
    "tax_luc_description",
])

# "CITY ST ZIP[-EXT]" — used by LGIM; Cuyahoga already splits city/state/zip
_ADDR_RE = re.compile(r"^(.*?)\s+([A-Z]{2})\s+(\d{5}(?:-\d{4})?)$")

_GOV_TERMS = (
    "CITY OF ", "COUNTY OF ", "STATE OF ", "UNITED STATES",
    "DEPT OF ", "DEPARTMENT OF ", "METRO PARK", "SCHOOL DIST",
    "METROPOLITAN", "TRANSIT", "PORT OF ", " ISD",
    "OHIO DOT", "ODOT",
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _ring_centroid(geometry):
    rings = (geometry or {}).get("rings", [])
    if not rings or not rings[0]:
        return None, None
    pts  = rings[0]
    lats = [p[1] for p in pts if len(p) >= 2]
    lngs = [p[0] for p in pts if len(p) >= 2]
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
            raise ValueError(f"Ohio GIS API error ({url}): {msg}")
        return data
    raise ValueError(f"Ohio GIS API throttled after {_MAX_RETRY} attempts")


def _paginate(url, where, out_fields, wgs84):
    features, offset = [], 0
    while True:
        params = {
            "where":             where,
            "outFields":         out_fields,
            "returnGeometry":    "true",
            "resultOffset":      offset,
            "resultRecordCount": _PAGE_SIZE,
            "orderByFields":     "OBJECTID",
            "f":                 "json",
        }
        if not wgs84:
            params["outSR"] = "4326"
        data  = _fetch_page(url, params)
        batch = data.get("features", [])
        features.extend(batch)
        if not data.get("exceededTransferLimit", False):
            break
        offset += len(batch)
        time.sleep(0.3)
    return features


def _parse_postal(raw):
    m = _ADDR_RE.match((raw or "").strip())
    return (m.group(1).strip(), m.group(2), m.group(3)) if m else ("", "", "")


# ---------------------------------------------------------------------------
# LGIM schema (Franklin County)
# ---------------------------------------------------------------------------

def _classify_lgim(classcd):
    c = (classcd or "").strip()
    if c == "490":
        return "Vacant"
    if c.startswith("5") and c.isdigit():
        return "Industrial" if 550 <= int(c) <= 599 else "Commercial"
    return None


def _where_lgim(classes, min_acres, max_acres, max_value):
    parts = []
    if "Commercial" in classes:
        parts.append("(CLASSCD >= '510' AND CLASSCD <= '549')")
    if "Industrial" in classes:
        parts.append("(CLASSCD >= '550' AND CLASSCD <= '599')")
    if "Vacant" in classes:
        parts.append("CLASSCD = '490'")
    if not parts:
        return None
    return (
        f"({' OR '.join(parts)})"
        f" AND STATEDAREA >= {min_acres} AND STATEDAREA <= {max_acres}"
        f" AND TOTVALUEBASE > 0 AND TOTVALUEBASE <= {max_value}"
    )


def _normalize_lgim(feat, classes, county_name):
    a = feat.get("attributes", {})
    lat, lng = _ring_centroid(feat.get("geometry"))
    if lat is None:
        return None
    owner = str(a.get("OWNERNME1") or "").strip()
    if any(t in owner.upper() for t in _GOV_TERMS):
        return None
    classcd = str(a.get("CLASSCD") or "").strip()
    pc = _classify_lgim(classcd)
    if pc not in classes:
        return None
    acres = float(a.get("STATEDAREA") or 0)
    owner_city, owner_state, owner_zip = _parse_postal(a.get("PSTLCITYSTZIP"))
    return {
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
        "county":         county_name,
        "luc_msg":        str(a.get("CLASSDSCRP") or classcd).strip(),
    }


# ---------------------------------------------------------------------------
# Cuyahoga County (FO_PP_CAMA schema)
# ---------------------------------------------------------------------------

def _fetch_county_cuyahoga(county_key, max_value, min_acres, max_acres, property_classes):
    """Cuyahoga County fetcher — FO_PP_CAMA schema (not LGIM)."""
    cfg  = _COUNTIES[county_key]
    url  = cfg["url"]
    name = cfg["name"]

    classes = property_classes or ["Commercial", "Industrial", "Vacant"]

    # property_class='C' = Commercial, 'I' = Industrial
    # Vacant = commercial/industrial land with no buildings
    pc_filter = "(property_class = 'C' OR property_class = 'I')"

    where = (
        f"{pc_filter}"
        f" AND parcel_acreage >= {min_acres} AND parcel_acreage <= {max_acres}"
        f" AND certified_tax_total > 0 AND certified_tax_total <= {max_value}"
    )

    rows   = []
    offset = 0
    while True:
        params = {
            "where":             where,
            "outFields":         _CUY_OUT_FIELDS,
            "returnGeometry":    "true",
            "resultOffset":      offset,
            "resultRecordCount": _PAGE_SIZE,
            "orderByFields":     "OBJECTID",
            "f":                 "json",
            # already WGS84 — no outSR needed
        }
        data  = _fetch_page(url, params)
        batch = data.get("features", [])

        for feat in batch:
            a   = feat.get("attributes", {})
            lat, lng = _ring_centroid(feat.get("geometry"))
            if lat is None:
                continue

            owner = str(a.get("parcel_owner") or a.get("mail_name") or "").strip()
            if any(t in owner.upper() for t in _GOV_TERMS):
                continue

            raw_pc     = str(a.get("property_class") or "").strip().upper()
            com_bldgs  = int(a.get("com_bldg_count") or 0)
            bldg_val   = float(a.get("certified_tax_building") or 0)
            acres      = float(a.get("parcel_acreage") or 0)

            # Classify
            is_vacant = (com_bldgs == 0 and bldg_val == 0)
            if raw_pc == "I":
                pc = "Industrial"
            else:
                pc = "Vacant" if is_vacant else "Commercial"

            if pc not in classes:
                continue

            # Parse par_addr_all: "4943 BANBURY CT, WARRENSVILLE HEIGHTS, OH, 44128"
            addr_raw   = str(a.get("par_addr_all") or "").strip()
            addr_parts = [p.strip() for p in addr_raw.split(",")]
            situs_addr = addr_parts[0] if addr_parts else addr_raw

            # Mailing address already split
            owner_city  = str(a.get("mail_city")  or "").strip()
            owner_state = str(a.get("mail_state") or "").strip().upper()
            owner_zip   = str(a.get("mail_zip")   or "").strip()

            rows.append({
                "parcel_id":      str(a.get("PARCELPIN") or "").strip(),
                "address":        situs_addr,
                "city":           addr_parts[1] if len(addr_parts) > 1 else "",
                "zip":            str(a.get("parcel_zip") or "").strip(),
                "property_class": pc,
                "land_sqft":      round(acres * 43560, 1),
                "land_acres":     round(acres, 4),
                "assessed_value": float(a.get("certified_tax_total") or 0),
                "owner_name":     owner,
                "owner_address":  str(a.get("mail_addr_street") or "").strip(),
                "owner_city":     owner_city,
                "owner_state":    owner_state,
                "owner_zip":      owner_zip,
                "lat":            round(lat, 6),
                "lng":            round(lng, 6),
                "out_of_state":   owner_state not in ("OH", ""),
                "county":         name,
                "luc_msg":        str(a.get("tax_luc_description") or a.get("prop_class_desc") or "").strip(),
            })

        if not data.get("exceededTransferLimit", False):
            break
        offset += len(batch)
        time.sleep(0.3)

    return rows


# ---------------------------------------------------------------------------
# Hamilton County (CAGIS schema)
# ---------------------------------------------------------------------------

def _classify_hamilton(exlucode, mktimp):
    ex = (exlucode or "").upper().strip()
    if ex in _HAM_EXLUCODE_COMMERCIAL:
        return "Commercial"
    if ex in _HAM_EXLUCODE_INDUSTRIAL:
        return "Industrial"
    if ex == "VA" and mktimp == 0:
        return "Vacant"
    return None


def _where_hamilton(classes, min_acres, max_acres, max_value):
    parts = []
    if "Commercial" in classes:
        parts.append("EXLUCODE IN ('C', 'O', 'MU', 'CH')")
    if "Industrial" in classes:
        parts.append("EXLUCODE IN ('LI', 'HI')")
    if "Vacant" in classes:
        parts.append("(EXLUCODE = 'VA' AND MKTIMP = 0)")
    if not parts:
        return None
    return (
        f"({' OR '.join(parts)})"
        f" AND ACREDEED >= {min_acres} AND ACREDEED <= {max_acres}"
        f" AND MKT_TOTAL_VAL > 0 AND MKT_TOTAL_VAL <= {max_value}"
    )


def _normalize_hamilton(feat, classes, county_name):
    a    = feat.get("attributes", {})
    lat, lng = _ring_centroid(feat.get("geometry"))
    if lat is None:
        return None

    owner = str(a.get("OWNNM1") or "").strip()
    if not owner:
        owner = str(a.get("OWNNM2") or "").strip()
    if any(t in owner.upper() for t in _GOV_TERMS):
        return None

    exlucode = str(a.get("EXLUCODE") or "").strip()
    mktimp   = float(a.get("MKTIMP") or 0)
    pc = _classify_hamilton(exlucode, mktimp)
    if pc not in classes:
        return None

    addrno = str(a.get("ADDRNO") or "").strip()
    addrst = str(a.get("ADDRST") or "").strip()
    addrsf = str(a.get("ADDRSF") or "").strip()
    address = " ".join(filter(None, [addrno, addrst, addrsf]))

    acres       = float(a.get("ACREDEED") or 0)
    owner_state = str(a.get("OWNADSTATE") or "").strip().upper()

    return {
        "parcel_id":      str(a.get("PARCELID") or "").strip(),
        "address":        address,
        "city":           "",
        "zip":            "",
        "property_class": pc,
        "land_sqft":      round(acres * 43560, 1),
        "land_acres":     round(acres, 4),
        "assessed_value": float(a.get("MKT_TOTAL_VAL") or 0),
        "owner_name":     owner,
        "owner_address":  str(a.get("OWNAD1") or "").strip(),
        "owner_city":     str(a.get("OWNADCITY") or "").strip(),
        "owner_state":    owner_state,
        "owner_zip":      str(a.get("OWNADZIP") or "").strip(),
        "lat":            round(lat, 6),
        "lng":            round(lng, 6),
        "out_of_state":   owner_state not in ("OH", ""),
        "county":         county_name,
        "luc_msg":        f"CLASS={a.get('CLASS')} {exlucode}",
    }


def _fetch_county_hamilton(county_key, max_value, min_acres, max_acres, property_classes):
    """Hamilton County (CAGIS) — browser headers required; EXLUCODE-based classification."""
    cfg    = _COUNTIES[county_key]
    url    = cfg["url"]
    name   = cfg["name"]
    classes = property_classes or ["Commercial", "Industrial", "Vacant"]

    where = _where_hamilton(classes, min_acres, max_acres, max_value)
    if where is None:
        return []

    rows   = []
    offset = 0
    while True:
        params = {
            "where":             where,
            "outFields":         _HAM_OUT_FIELDS,
            "returnGeometry":    "true",
            "outSR":             "4326",
            "resultOffset":      offset,
            "resultRecordCount": _PAGE_SIZE,
            "orderByFields":     "OBJECTID",
            "f":                 "json",
        }
        qs  = urllib.parse.urlencode(params)
        req = urllib.request.Request(f"{url}?{qs}", headers=_HAM_HEADERS)
        for attempt in range(1, _MAX_RETRY + 1):
            try:
                with urllib.request.urlopen(req, timeout=45) as r:
                    data = json.loads(r.read())
                break
            except (urllib.error.URLError, TimeoutError, ConnectionResetError):
                if attempt < _MAX_RETRY:
                    time.sleep(2 ** attempt + random.uniform(0, 1))
                    continue
                raise
        if "error" in data:
            raise ValueError(f"Hamilton GIS error: {data['error'].get('message', data['error'])}")

        batch = data.get("features", [])
        for feat in batch:
            row = _normalize_hamilton(feat, classes, name)
            if row is not None:
                rows.append(row)

        if not data.get("exceededTransferLimit", False):
            break
        offset += len(batch)
        time.sleep(0.4)

    return rows


# ---------------------------------------------------------------------------
# Schema dispatch
# ---------------------------------------------------------------------------

def _fetch_county(county_key, max_value, min_acres, max_acres, property_classes):
    cfg = _COUNTIES[county_key]

    if cfg["schema"] == "cuyahoga":
        return _fetch_county_cuyahoga(county_key, max_value, min_acres, max_acres, property_classes)

    if cfg["schema"] == "hamilton":
        return _fetch_county_hamilton(county_key, max_value, min_acres, max_acres, property_classes)

    # LGIM schema (Franklin)
    name  = cfg["name"]
    where = _where_lgim(property_classes, min_acres, max_acres, max_value)
    if where is None:
        return []
    features = _paginate(cfg["url"], where, _OUT_FIELDS_LGIM, cfg["wgs84"])
    rows = []
    for feat in features:
        row = _normalize_lgim(feat, property_classes, name)
        if row is not None:
            rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_EMPTY_COLS = [
    "parcel_id", "address", "city", "zip", "property_class",
    "land_sqft", "land_acres", "assessed_value",
    "owner_name", "owner_address", "owner_city", "owner_state", "owner_zip",
    "lat", "lng", "out_of_state", "county", "luc_msg",
]


def fetch_parcels(city_cfg, property_classes, max_value, min_acres, max_acres):
    counties = city_cfg.get("counties", ["franklin"])
    all_rows = []

    for county_key in counties:
        if county_key not in _COUNTIES:
            print(f"[ohio] Skipping {county_key} — endpoint not yet verified")
            continue
        try:
            schema = _COUNTIES[county_key].get("schema", "lgim")
            print(f"[ohio] fetching {_COUNTIES[county_key]['name']} (schema={schema})...")
            rows = _fetch_county(county_key, max_value, min_acres, max_acres, property_classes)
            print(f"[ohio]   → {len(rows)} parcels")
            all_rows.extend(rows)
        except Exception as e:
            print(f"[ohio] WARNING: {_COUNTIES[county_key]['name']} failed: {e}")
        time.sleep(0.4)

    if not all_rows:
        print("[ohio] WARNING: 0 parcels — verify CLASSCD / property_class values")
        return pd.DataFrame(columns=_EMPTY_COLS)

    df = pd.DataFrame(all_rows)
    df = df.drop_duplicates(subset="parcel_id", keep="first")
    return df.dropna(subset=["lat", "lng"]).reset_index(drop=True)
