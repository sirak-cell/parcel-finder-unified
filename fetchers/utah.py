"""
Utah parcel fetcher — UGRC LIR FeatureServer (Salt Lake + Davis counties).

Normalized output schema:
  parcel_id, address, city, zip, property_class, land_sqft, land_acres,
  assessed_value, owner_name, owner_address, owner_city, owner_state,
  owner_zip, lat, lng, out_of_state, county, luc_msg
"""

import random
import time

import pandas as pd
import requests

HEADERS = {"User-Agent": "ParcelFinderBot/1.0 (internal drone-hub research tool)"}
PAGE_SIZE = 2000
OWNER_BATCH_SIZE = 100

SLC_ENDPOINT  = "https://services1.arcgis.com/99lidPhWCzftIe9K/ArcGIS/rest/services/Parcels_SaltLake_LIR/FeatureServer/0"
DAVIS_ENDPOINT = "https://services1.arcgis.com/99lidPhWCzftIe9K/ArcGIS/rest/services/Parcels_Davis_LIR/FeatureServer/0"
SLC_OWNER_URL  = "https://apps.saltlakecounty.gov/arcgis/rest/services/Assessor/Parcel_Viewer_external/MapServer/5/query"
DAVIS_OWNER_URL = "https://gisportal-pro.daviscountyutah.gov/server/rest/services/Public/Davis_County_Public_Parcels/MapServer/0/query"

UGRC_ENDPOINTS = {
    "Salt Lake County": SLC_ENDPOINT,
    "Davis County":     DAVIS_ENDPOINT,
}

_TYPE_PATTERNS = {
    "Commercial": "UPPER(PROP_CLASS) LIKE '%COMMERCIAL%'",
    "Industrial":  "UPPER(PROP_CLASS) LIKE '%INDUSTRIAL%'",
    "Vacant":      "UPPER(PROP_CLASS) = 'VACANT'",
}

_MAX_RETRIES = 5
_BACKOFF_BASE = 2.0
_PAGE_DELAY   = 0.5


def _build_where(max_value, min_acres, max_acres, property_classes, ugrc_cities):
    types = property_classes or list(_TYPE_PATTERNS.keys())

    # Per-class building filter: Vacant = no recorded building; C/I = allow small structures
    parts = []
    for t in types:
        if t not in _TYPE_PATTERNS:
            continue
        bldg = "BLDG_SQFT IS NULL" if t == "Vacant" else "(BLDG_SQFT IS NULL OR BLDG_SQFT <= 500)"
        parts.append(f"({_TYPE_PATTERNS[t]} AND {bldg})")
    prop_expr = "(" + " OR ".join(parts) + ")" if parts else "1=1"

    exclusions = (
        " AND UPPER(PROP_CLASS) NOT LIKE '%APARTMENT%'"
        " AND UPPER(PROP_CLASS) NOT LIKE '%CONDO%'"
        " AND UPPER(PROP_CLASS) NOT LIKE '%RESIDENTIAL%'"
        " AND (TAXEXEMPT_TYPE IS NULL OR UPPER(TAXEXEMPT_TYPE) NOT LIKE '%GOVERNMENT%')"
    )
    where = (
        f"{prop_expr}{exclusions}"
        f" AND TOTAL_MKT_VALUE > 0 AND TOTAL_MKT_VALUE <= {max_value}"
        f" AND PARCEL_ACRES >= {min_acres} AND PARCEL_ACRES <= {max_acres}"
    )
    if ugrc_cities:
        city_list = ",".join(f"'{c.upper()}'" for c in ugrc_cities)
        where += f" AND UPPER(PARCEL_CITY) IN ({city_list})"
    return where


def _is_rate_limit(data):
    msg = str(data.get("error", {}).get("message", "")).lower()
    code = data.get("error", {}).get("code", 0)
    return "too many requests" in msg or code in (429, 503)


def _fetch_page(service_url, county_name, params):
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            resp = requests.get(f"{service_url}/query", params=params, headers=HEADERS, timeout=30)
            if resp.status_code in (429, 503):
                raise requests.HTTPError(response=resp)
            resp.raise_for_status()
            data = resp.json()
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as exc:
            err_hint = str(exc)
        else:
            if "error" not in data:
                return data
            if _is_rate_limit(data):
                err_hint = data["error"].get("message", str(data["error"]))
            else:
                raise ValueError(f"UGRC API error for {county_name}: {data['error'].get('message', data['error'])}")

        if attempt == _MAX_RETRIES:
            raise ValueError(f"UGRC rate-limited for {county_name} after {_MAX_RETRIES} attempts: {err_hint}")
        time.sleep(_BACKOFF_BASE * (2 ** (attempt - 1)) + random.uniform(0, 1))
    raise RuntimeError("retry loop exited unexpectedly")


def _map_prop_class(prop_class_str):
    pc = prop_class_str.upper()
    if "INDUSTRIAL" in pc:
        return "Industrial"
    if "COMMERCIAL" in pc:
        return "Commercial"
    if "VACANT" in pc:
        return "Vacant"
    return "Commercial"


def _fetch_county(service_url, county_name, where):
    rows = []
    offset = 0
    while True:
        params = {
            "where": where,
            "outFields": (
                "PARCEL_ID,SERIAL_NUM,PARCEL_ADD,PARCEL_CITY,PROP_CLASS,"
                "TOTAL_MKT_VALUE,LAND_MKT_VALUE,PARCEL_ACRES,BLDG_SQFT"
            ),
            "returnCentroid": "true",
            "returnGeometry": "false",
            "resultOffset": offset,
            "resultRecordCount": PAGE_SIZE,
            "orderByFields": "OBJECTID",
            "outSR": "4326",
            "f": "json",
        }
        data = _fetch_page(service_url, county_name, params)
        features = data.get("features", [])
        for f in features:
            a = f["attributes"]
            c = f.get("centroid") or {}
            acres = a.get("PARCEL_ACRES") or 0.0
            rows.append({
                "parcel_id":    str(a.get("PARCEL_ID") or "").strip(),
                "_serial_num":  str(a.get("SERIAL_NUM") or "").strip(),
                "address":      str(a.get("PARCEL_ADD") or "").strip(),
                "city":         str(a.get("PARCEL_CITY") or "").strip(),
                "zip":          "",
                "property_class": _map_prop_class(str(a.get("PROP_CLASS") or "")),
                "land_sqft":    round(acres * 43560, 1),
                "land_acres":   round(acres, 4),
                "assessed_value": float(a.get("TOTAL_MKT_VALUE") or 0),
                "lat":          c.get("y"),
                "lng":          c.get("x"),
                "county":       county_name,
                "luc_msg":      str(a.get("PROP_CLASS") or "").strip(),
            })
        if not data.get("exceededTransferLimit", False):
            break
        offset += len(features)
        time.sleep(_PAGE_DELAY)
    return rows


def _enrich_slc_owners(df):
    if df.empty:
        for col in ["owner_name", "owner_address", "owner_city", "owner_state", "owner_zip", "out_of_state"]:
            df[col] = "" if col != "out_of_state" else False
        return df

    ids = df["parcel_id"].tolist()
    owner_map = {}
    for i in range(0, len(ids), OWNER_BATCH_SIZE):
        chunk = ids[i : i + OWNER_BATCH_SIZE]
        id_clause = "','".join(chunk)
        params = {
            "where": f"parcel_id IN ('{id_clause}')",
            "outFields": "parcel_id,own_name,own_addr,own_citystate,own_zip",
            "returnGeometry": "false",
            "f": "json",
        }
        try:
            resp = requests.post(
                SLC_OWNER_URL, data=params,
                headers={**HEADERS, "Content-Type": "application/x-www-form-urlencoded"},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            if "error" in data:
                continue
            for feat in data.get("features", []):
                a = feat["attributes"]
                pid = str(a.get("parcel_id") or "").strip()
                raw_name = str(a.get("own_name") or "").strip()
                cs = str(a.get("own_citystate") or "").strip()
                cs_parts = cs.split(",", 1)
                owner_map[pid] = {
                    "owner_name":    "" if raw_name.upper() == "NULL" else raw_name,
                    "owner_address": str(a.get("own_addr") or "").strip(),
                    "owner_city":    cs_parts[0].strip() if cs_parts else "",
                    "owner_state":   cs_parts[1].strip() if len(cs_parts) > 1 else "",
                    "owner_zip":     str(a.get("own_zip") or "").strip(),
                }
        except Exception:
            pass

    for col in ["owner_name", "owner_address", "owner_city", "owner_state", "owner_zip"]:
        df[col] = df["parcel_id"].map(lambda pid: owner_map.get(pid, {}).get(col, ""))
    df["out_of_state"] = df["owner_state"].str.upper().apply(lambda s: s not in ("UT", "UTAH", ""))
    return df


def _enrich_davis_owners(df):
    if df.empty:
        for col in ["owner_name", "owner_address", "owner_city", "owner_state", "owner_zip", "out_of_state"]:
            df[col] = "" if col != "out_of_state" else False
        return df

    ids = df["parcel_id"].tolist()
    addr_map = {}
    for i in range(0, len(ids), OWNER_BATCH_SIZE):
        chunk = [str(x) for x in ids[i : i + OWNER_BATCH_SIZE]]
        id_clause = "','".join(chunk)
        params = {
            "where": f"ParcelTaxID IN ('{id_clause}')",
            "outFields": (
                "ParcelTaxID,"
                "ParcelOwnerMailAddressLine1,ParcelOwnerMailAddressLine2,ParcelOwnerMailAddressLine3,"
                "ParcelOwnerMailCity,ParcelOwnerMailState,ParcelOwnerMailZipcode"
            ),
            "returnGeometry": "false",
            "f": "json",
        }
        try:
            resp = requests.get(DAVIS_OWNER_URL, params=params, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            if "error" in data:
                continue
            for feat in data.get("features", []):
                a = feat["attributes"]
                tid = str(a.get("ParcelTaxID") or "").strip()
                line1 = str(a.get("ParcelOwnerMailAddressLine1") or "").strip()
                line2 = str(a.get("ParcelOwnerMailAddressLine2") or "").strip()
                line3 = str(a.get("ParcelOwnerMailAddressLine3") or "").strip()
                addr = " ".join(p for p in [line1, line2, line3] if p)
                city  = str(a.get("ParcelOwnerMailCity") or "").strip()
                state = str(a.get("ParcelOwnerMailState") or "").strip()
                addr_map[tid] = {
                    "owner_name":    "See Davis County Portal",
                    "owner_address": addr,
                    "owner_city":    city,
                    "owner_state":   state,
                    "owner_zip":     str(a.get("ParcelOwnerMailZipcode") or "").strip(),
                }
        except Exception:
            pass

    for col in ["owner_name", "owner_address", "owner_city", "owner_state", "owner_zip"]:
        df[col] = df["parcel_id"].map(lambda pid: addr_map.get(str(pid), {}).get(col, ""))
    df["out_of_state"] = df["owner_state"].str.upper().apply(lambda s: s not in ("UT", "UTAH", ""))
    return df


def fetch_parcels(city_cfg, property_classes, max_value, min_acres, max_acres):
    ugrc_cities = city_cfg.get("ugrc_cities")
    where = _build_where(max_value, min_acres, max_acres, property_classes, ugrc_cities)

    all_rows = []
    for county, url in UGRC_ENDPOINTS.items():
        all_rows.extend(_fetch_county(url, county, where))
        time.sleep(_PAGE_DELAY)

    if not all_rows:
        return pd.DataFrame(columns=[
            "parcel_id", "address", "city", "zip", "property_class",
            "land_sqft", "land_acres", "assessed_value",
            "owner_name", "owner_address", "owner_city", "owner_state", "owner_zip",
            "lat", "lng", "out_of_state", "county", "luc_msg",
        ])

    df = pd.DataFrame(all_rows)
    df = df.dropna(subset=["lat", "lng"]).drop_duplicates(subset=["parcel_id"]).reset_index(drop=True)

    slc_df   = df[df["county"] == "Salt Lake County"].copy()
    davis_df = df[df["county"] == "Davis County"].copy()

    slc_enriched   = _enrich_slc_owners(slc_df)
    davis_enriched = _enrich_davis_owners(davis_df)

    result = pd.concat([slc_enriched, davis_enriched], ignore_index=True)
    result = result.drop(columns=["_serial_num"], errors="ignore")
    return result.reset_index(drop=True)
