"""
Texas parcel fetcher — 4 sub-markets:
  dallas      → TxDOT 2025_Land_Parcels FeatureServer/328 (OID-range chunking)
  fort_worth  → TAD OD_ParcelView MapServer/0 (OID-range, ring centroid, no values)
  san_antonio → BCAD_Parcels_PROD FeatureServer/0 (offset, returnCentroid)
  houston     → TxDOT 2025_Land_Parcels FeatureServer/328, COUNTY='HARRIS' (offset, stop on 400)
"""

import json
import random
import socket
import time
import urllib.parse
import urllib.request

import pandas as pd

TXDOT_ENDPOINT = (
    "https://services.arcgis.com/KTcxiTD9dsQw4r7Z/arcgis/rest/services"
    "/2025_Land_Parcels/FeatureServer/328"
)
BCAD_ENDPOINT = (
    "https://gis.sara-tx.org/ags1/rest/services/FW_Bexar"
    "/BCAD_Parcels_PROD/FeatureServer/0"
)
TAD_ENDPOINT = (
    "https://tad.newedgeservices.com/arcgis/rest/services"
    "/OD_TAD/OD_ParcelView/MapServer/0"
)

HEADERS = {"User-Agent": "ParcelFinderBot/1.0 (internal drone-hub research tool)"}
PAGE_SIZE = 2000

_RETRY_CODES = {429, 500, 503}
_MAX_ATTEMPTS = 5

# Dallas / Houston OID range in TxDOT statewide layer
_DALLAS_OID_LO, _DALLAS_OID_HI, _DALLAS_OID_STEP = 2_850_000, 3_650_000, 20_000
# Fort Worth OID range in TAD layer
_TARRANT_OID_LO, _TARRANT_OID_HI, _TARRANT_OID_STEP = 0, 760_000, 10_000

_GOV_DALLAS = (
    "CITY OF DALLAS", "DALLAS COUNTY", "STATE OF TEXAS",
    "UNITED STATES", "DALLAS ISD", "DALLAS HOUSING",
    "TXDOT", "TX DEPT OF", "TEXAS DEPARTMENT",
)
_GOV_TARRANT = (
    "CITY OF FORT WORTH", "CITY OF ARLINGTON", "TARRANT COUNTY",
    "STATE OF TEXAS", "UNITED STATES", "FORT WORTH ISD",
    "ARLINGTON ISD", "TARRANT REGIONAL WATER",
    "TXDOT", "TX DEPT OF", "TEXAS DEPARTMENT",
)
_GOV_BEXAR = (
    "CITY OF SAN ANTONIO", "BEXAR COUNTY", "STATE OF TEXAS",
    "SAN ANTONIO WATER SYSTEM", "UNITED STATES", "SAN ANTONIO ISD",
    "VIA METROPOLITAN", "CPS ENERGY", "SAWS",
)
_GOV_HOUSTON = (
    "CITY OF HOUSTON", "HARRIS COUNTY", "STATE OF TEXAS",
    "UNITED STATES", "HOUSTON ISD", "HISD",
    "TXDOT", "TX DEPT OF", "TEXAS DEPARTMENT",
    "PORT OF HOUSTON", "METRO TRANSIT",
)


# ── HTTP helpers ───────────────────────────────────────────────────────────────

def _post(base, params, timeout=90):
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(
        f"{base}/query", data=data,
        headers={**HEADERS, "Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _fetch_page(base, params, stop_on_400=False):
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            d = _post(base, params)
        except urllib.error.HTTPError as e:
            if e.code == 400 and stop_on_400:
                return None  # Houston offset-based: treat as end of data
            if e.code in _RETRY_CODES and attempt < _MAX_ATTEMPTS:
                time.sleep(2 ** attempt + random.uniform(0, 1))
                continue
            raise
        except (urllib.error.URLError, TimeoutError, socket.timeout):
            if attempt < _MAX_ATTEMPTS:
                time.sleep(2 ** attempt + random.uniform(0, 1))
                continue
            raise
        if "error" in d:
            msg = d["error"].get("message", str(d["error"]))
            code = d["error"].get("code", 0)
            if code == 400 and stop_on_400:
                return None
            retryable = code in _RETRY_CODES or (
                code == 400 and "cannot perform query" in msg.lower()
            )
            if retryable and attempt < _MAX_ATTEMPTS:
                time.sleep(2 ** attempt + random.uniform(0, 1))
                continue
            raise ValueError(f"API error: {msg}")
        return d
    raise ValueError("API throttled — max retries exceeded")


def _ring_centroid(geometry):
    rings = (geometry or {}).get("rings", [])
    if not rings or not rings[0]:
        return None, None
    pts = rings[0]
    return sum(p[1] for p in pts) / len(pts), sum(p[0] for p in pts) / len(pts)


def _parse_acres(legal_area):
    try:
        return float(str(legal_area or "").strip().split()[0])
    except (ValueError, IndexError):
        return 0.0


def _parse_value(v):
    try:
        return float(str(v or "0").replace(",", "").replace("$", "").strip())
    except (ValueError, TypeError):
        return 0.0


def _is_gov(owner, terms):
    upper = (owner or "").upper()
    return any(t in upper for t in terms)


# ── Dallas fetcher (TxDOT OID-range) ──────────────────────────────────────────

_DALLAS_BASE_WHERE = (
    "COUNTY='DALLAS'"
    " AND LOC_LAND_USE='COM'"
    " AND (STAT_LAND_USE LIKE 'F%' OR STAT_LAND_USE LIKE 'C%')"
)
_DALLAS_FIELDS = (
    "Prop_ID,OWNER_NAME,SITUS_ADDR,SITUS_CITY,SITUS_ZIP,"
    "MAIL_LINE1,MAIL_LINE2,MAIL_CITY,MAIL_STAT,MAIL_ZIP,"
    "STAT_LAND_USE,LOC_LAND_USE,LEGAL_AREA,LAND_VALUE,MKT_VALUE"
)


def _fetch_dallas(max_value, min_acres, max_acres, property_classes):
    rows = []
    for oid_lo in range(_DALLAS_OID_LO, _DALLAS_OID_HI, _DALLAS_OID_STEP):
        oid_hi = oid_lo + _DALLAS_OID_STEP
        chunk_where = f"OBJECTID >= {oid_lo} AND OBJECTID < {oid_hi} AND ({_DALLAS_BASE_WHERE})"
        offset = 0
        while True:
            params = {
                "where": chunk_where,
                "outFields": _DALLAS_FIELDS,
                "returnCentroid": "true",
                "returnGeometry": "false",
                "outSR": "4326",
                "resultOffset": offset,
                "resultRecordCount": PAGE_SIZE,
                "f": "json",
            }
            d = _fetch_page(TXDOT_ENDPOINT, params)
            if d is None:
                break
            features = d.get("features", [])
            for f in features:
                a = f["attributes"]
                owner = str(a.get("OWNER_NAME") or "").strip()
                if _is_gov(owner, _GOV_DALLAS):
                    continue
                acres = _parse_acres(a.get("LEGAL_AREA") or "")
                if acres < min_acres or acres > max_acres:
                    continue
                val = _parse_value(a.get("MKT_VALUE"))
                if val <= 0 or val > max_value:
                    continue
                stat = str(a.get("STAT_LAND_USE") or "").strip()
                if stat.startswith("F"):
                    prop_class = "Commercial"
                else:
                    prop_class = "Vacant"
                if prop_class not in (property_classes or ["Commercial", "Industrial", "Vacant"]):
                    continue
                c = f.get("centroid") or {}
                mail_parts = [
                    str(a.get("MAIL_LINE1") or "").strip(),
                    str(a.get("MAIL_LINE2") or "").strip(),
                ]
                mail_street = " ".join(p for p in mail_parts if p)
                owner_city  = str(a.get("MAIL_CITY") or "").strip()
                owner_state = str(a.get("MAIL_STAT") or "").strip()
                rows.append({
                    "parcel_id":      str(a.get("Prop_ID") or "").strip(),
                    "address":        str(a.get("SITUS_ADDR") or "").strip(),
                    "city":           str(a.get("SITUS_CITY") or "").strip(),
                    "zip":            str(a.get("SITUS_ZIP") or "").strip(),
                    "property_class": prop_class,
                    "land_sqft":      round(acres * 43560, 1),
                    "land_acres":     round(acres, 4),
                    "assessed_value": val,
                    "owner_name":     owner,
                    "owner_address":  mail_street,
                    "owner_city":     owner_city,
                    "owner_state":    owner_state,
                    "owner_zip":      str(a.get("MAIL_ZIP") or "").strip(),
                    "lat":            c.get("y"),
                    "lng":            c.get("x"),
                    "out_of_state":   owner_state.upper() not in ("TX", "TEXAS", ""),
                    "county":         "Dallas County, TX",
                    "luc_msg":        f"{stat} / {str(a.get('LOC_LAND_USE') or '').strip()}",
                })
            if not d.get("exceededTransferLimit", False):
                break
            offset += len(features)
            time.sleep(0.4)
        time.sleep(0.3)
    return rows


# ── Fort Worth fetcher (TAD OID-range, no values) ─────────────────────────────

_TARRANT_FIELDS = (
    "Account_Nu,Owner_Name,Owner_Addr,Owner_City,Owner_Zip,"
    "Situs_Addr,CALCULATED,Land_SqFt,State_Use_,Deed_Date"
)


def _fetch_fortworth(min_acres, max_acres, property_classes):
    rows = []
    classes = property_classes or ["Commercial", "Industrial", "Vacant"]
    use_codes = []
    if "Commercial" in classes:
        use_codes.append("'F1'")
    if "Industrial" in classes:
        use_codes.append("'F2'")
    if not use_codes:
        return rows
    use_in = ",".join(use_codes)
    where_base = f"State_Use_ IN ({use_in}) AND CALCULATED >= {min_acres} AND CALCULATED <= {max_acres}"

    for oid_lo in range(_TARRANT_OID_LO, _TARRANT_OID_HI, _TARRANT_OID_STEP):
        oid_hi = oid_lo + _TARRANT_OID_STEP
        chunk_where = f"OBJECTID >= {oid_lo} AND OBJECTID < {oid_hi} AND ({where_base})"
        params = {
            "where": chunk_where,
            "outFields": _TARRANT_FIELDS,
            "returnGeometry": "true",
            "outSR": "4326",
            "f": "json",
        }
        d = _fetch_page(TAD_ENDPOINT, params)
        if d is None:
            continue
        for f in d.get("features", []):
            a = f["attributes"]
            owner = str(a.get("Owner_Name") or "").strip()
            if _is_gov(owner, _GOV_TARRANT):
                continue
            lat, lng = _ring_centroid(f.get("geometry") or {})
            if lat is None:
                continue
            state_use = str(a.get("State_Use_") or "").strip()
            prop_class = "Commercial" if state_use == "F1" else "Industrial"
            acres = float(a.get("CALCULATED") or 0)
            sqft_raw = str(a.get("Land_SqFt") or "").strip()
            try:
                sqft = int(float(sqft_raw)) if sqft_raw else round(acres * 43560)
            except ValueError:
                sqft = round(acres * 43560)
            owner_city_raw = str(a.get("Owner_City") or "").strip()
            cs_parts = owner_city_raw.rsplit(" ", 2) if owner_city_raw else []
            if len(cs_parts) >= 2 and len(cs_parts[-1]) == 2:
                owner_city  = " ".join(cs_parts[:-1])
                owner_state = cs_parts[-1]
            else:
                owner_city  = owner_city_raw
                owner_state = ""
            rows.append({
                "parcel_id":      str(a.get("Account_Nu") or "").strip(),
                "address":        str(a.get("Situs_Addr") or "").strip(),
                "city":           "Fort Worth",
                "zip":            "",
                "property_class": prop_class,
                "land_sqft":      float(sqft),
                "land_acres":     round(acres, 4),
                "assessed_value": 0.0,
                "owner_name":     owner,
                "owner_address":  str(a.get("Owner_Addr") or "").strip(),
                "owner_city":     owner_city,
                "owner_state":    owner_state,
                "owner_zip":      str(a.get("Owner_Zip") or "").strip(),
                "lat":            lat,
                "lng":            lng,
                "out_of_state":   owner_state.upper() not in ("TX", "TEXAS", ""),
                "county":         "Tarrant County, TX",
                "luc_msg":        state_use,
            })
        time.sleep(0.1)
    return rows


# ── San Antonio fetcher (BCAD, offset-based, returnCentroid) ──────────────────

_BCAD_FIELDS = (
    "Geo_id,Owner_name,Addr_line1,Addr_line2,Addr_city,Addr_state,Zip,"
    "Situs_num,Situs_street_prefix,Situs_street,Situs_street_sufix,Situs_unit,"
    "City,Situs_Zip,State_cd,Market_val,Land_acres,Sq_ft"
)


def _fetch_bexar(max_value, min_acres, max_acres, property_classes):
    classes = property_classes or ["Commercial", "Industrial", "Vacant"]
    codes = []
    if "Commercial" in classes:
        codes.append("'F1'")
    if "Industrial" in classes:
        codes.append("'F2'")
    if not codes:
        return []
    codes_in = ",".join(codes)
    where = (
        f"State_cd IN ({codes_in})"
        f" AND Land_acres >= {min_acres} AND Land_acres <= {max_acres}"
        f" AND Market_val > 0 AND Market_val <= {max_value}"
    )
    rows = []
    offset = 0
    while True:
        params = {
            "where": where,
            "outFields": _BCAD_FIELDS,
            "returnCentroid": "true",
            "returnGeometry": "false",
            "outSR": "4326",
            "resultOffset": offset,
            "resultRecordCount": PAGE_SIZE,
            "orderByFields": "OBJECTID",
            "f": "json",
        }
        d = _fetch_page(BCAD_ENDPOINT, params)
        if d is None:
            break
        features = d.get("features", [])
        for f in features:
            a = f["attributes"]
            owner = str(a.get("Owner_name") or "").strip()
            if _is_gov(owner, _GOV_BEXAR):
                continue
            c = f.get("centroid") or {}
            lat, lng = c.get("y"), c.get("x")
            state_cd = str(a.get("State_cd") or "").strip()
            prop_class = "Industrial" if state_cd == "F2" else "Commercial"
            situs_parts = [
                str(a.get("Situs_num") or "").strip(),
                str(a.get("Situs_street_prefix") or "").strip(),
                str(a.get("Situs_street") or "").strip(),
                str(a.get("Situs_street_sufix") or "").strip(),
                str(a.get("Situs_unit") or "").strip(),
            ]
            situs_addr = " ".join(p for p in situs_parts if p.strip())
            acres = float(a.get("Land_acres") or 0)
            owner_city  = str(a.get("Addr_city") or "").strip()
            owner_state = str(a.get("Addr_state") or "").strip()
            rows.append({
                "parcel_id":      str(a.get("Geo_id") or "").strip(),
                "address":        situs_addr,
                "city":           str(a.get("City") or "SAN ANTONIO").strip(),
                "zip":            str(a.get("Situs_Zip") or "").strip(),
                "property_class": prop_class,
                "land_sqft":      float(a.get("Sq_ft") or round(acres * 43560)),
                "land_acres":     round(acres, 4),
                "assessed_value": float(a.get("Market_val") or 0),
                "owner_name":     owner,
                "owner_address":  " ".join(filter(None, [
                                      str(a.get("Addr_line1") or "").strip(),
                                      str(a.get("Addr_line2") or "").strip(),
                                  ])),
                "owner_city":     owner_city,
                "owner_state":    owner_state,
                "owner_zip":      str(a.get("Zip") or "").strip(),
                "lat":            lat,
                "lng":            lng,
                "out_of_state":   owner_state.upper() not in ("TX", "TEXAS", ""),
                "county":         "Bexar County, TX",
                "luc_msg":        state_cd,
            })
        if not d.get("exceededTransferLimit", False):
            break
        offset += len(features)
        time.sleep(0.4)
    return rows


# ── Houston fetcher (TxDOT, COUNTY='HARRIS', offset-based) ────────────────────

_HOUSTON_BASE_WHERE = (
    "COUNTY='HARRIS'"
    " AND LOC_LAND_USE='COM'"
    " AND (STAT_LAND_USE LIKE 'F%' OR STAT_LAND_USE LIKE 'C%')"
)
_HOUSTON_FIELDS = _DALLAS_FIELDS  # same TxDOT layer, same field names


def _fetch_houston(max_value, min_acres, max_acres, property_classes):
    rows = []
    offset = 0
    while True:
        params = {
            "where": _HOUSTON_BASE_WHERE,
            "outFields": _HOUSTON_FIELDS,
            "returnCentroid": "true",
            "returnGeometry": "false",
            "outSR": "4326",
            "resultOffset": offset,
            "resultRecordCount": PAGE_SIZE,
            "orderByFields": "OBJECTID",
            "f": "json",
        }
        d = _fetch_page(TXDOT_ENDPOINT, params, stop_on_400=True)
        if d is None:
            break  # 400 → end of usable range
        features = d.get("features", [])
        for f in features:
            a = f["attributes"]
            owner = str(a.get("OWNER_NAME") or "").strip()
            if _is_gov(owner, _GOV_HOUSTON):
                continue
            acres = _parse_acres(a.get("LEGAL_AREA") or "")
            if acres < min_acres or acres > max_acres:
                continue
            val = _parse_value(a.get("MKT_VALUE"))
            if val <= 0 or val > max_value:
                continue
            stat = str(a.get("STAT_LAND_USE") or "").strip()
            prop_class = "Commercial" if stat.startswith("F") else "Vacant"
            if prop_class not in (property_classes or ["Commercial", "Industrial", "Vacant"]):
                continue
            c = f.get("centroid") or {}
            mail_parts = [
                str(a.get("MAIL_LINE1") or "").strip(),
                str(a.get("MAIL_LINE2") or "").strip(),
            ]
            mail_street = " ".join(p for p in mail_parts if p)
            owner_city  = str(a.get("MAIL_CITY") or "").strip()
            owner_state = str(a.get("MAIL_STAT") or "").strip()
            rows.append({
                "parcel_id":      str(a.get("Prop_ID") or "").strip(),
                "address":        str(a.get("SITUS_ADDR") or "").strip(),
                "city":           str(a.get("SITUS_CITY") or "").strip(),
                "zip":            str(a.get("SITUS_ZIP") or "").strip(),
                "property_class": prop_class,
                "land_sqft":      round(acres * 43560, 1),
                "land_acres":     round(acres, 4),
                "assessed_value": val,
                "owner_name":     owner,
                "owner_address":  mail_street,
                "owner_city":     owner_city,
                "owner_state":    owner_state,
                "owner_zip":      str(a.get("MAIL_ZIP") or "").strip(),
                "lat":            c.get("y"),
                "lng":            c.get("x"),
                "out_of_state":   owner_state.upper() not in ("TX", "TEXAS", ""),
                "county":         "Harris County, TX",
                "luc_msg":        f"{stat} / COM",
            })
        if not d.get("exceededTransferLimit", False):
            break
        offset += len(features)
        time.sleep(0.4)
    return rows


# ── Router ────────────────────────────────────────────────────────────────────

def fetch_parcels(city_cfg, property_classes, max_value, min_acres, max_acres):
    market = city_cfg.get("market", "dallas")

    if market == "dallas":
        rows = _fetch_dallas(max_value, min_acres, max_acres, property_classes)
    elif market == "fort_worth":
        rows = _fetch_fortworth(min_acres, max_acres, property_classes)
    elif market == "san_antonio":
        rows = _fetch_bexar(max_value, min_acres, max_acres, property_classes)
    elif market == "houston":
        rows = _fetch_houston(max_value, min_acres, max_acres, property_classes)
    else:
        raise ValueError(f"Unknown Texas market: {market!r}")

    empty_cols = [
        "parcel_id", "address", "city", "zip", "property_class",
        "land_sqft", "land_acres", "assessed_value",
        "owner_name", "owner_address", "owner_city", "owner_state", "owner_zip",
        "lat", "lng", "out_of_state", "county", "luc_msg",
    ]
    if not rows:
        return pd.DataFrame(columns=empty_cols)

    df = pd.DataFrame(rows)
    df = df.dropna(subset=["lat", "lng"]).drop_duplicates(subset=["parcel_id"])
    df = df[df["address"].str.match(r"^\d", na=False)]
    return df.reset_index(drop=True)
