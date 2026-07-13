"""
Pennsylvania parcel fetcher.

Cities:
  Philadelphia  — OPA_PROPERTIES_PUBLIC ArcGIS FeatureServer
  Pittsburgh    — WPRDC CKAN (assessments + centroid join)

Normalized output schema matches all other fetchers.
"""

import time
import random

import pandas as pd
import requests

HEADERS = {"User-Agent": "ParcelFinderBot/1.0 (internal drone-hub research tool)"}
PAGE_SIZE = 2000
CKAN_SQL_URL = "https://data.wprdc.org/api/3/action/datastore_search_sql"

# Philadelphia OPA
PHILLY_URL = (
    "https://services.arcgis.com/fLeGjb7u4uXqeF9q/"
    "arcgis/rest/services/OPA_PROPERTIES_PUBLIC/FeatureServer/0"
)

# Pittsburgh / Allegheny WPRDC CKAN resource IDs
ASSESS_RID   = "9a1c60bd-f9f7-4aba-aeb7-af8c3aaa44e5"
CENTROID_RID = "3fab7152-3f11-4788-8372-4c33f86ea813"

# Philadelphia OPA category_code → property_class
_PHILLY_COMM_CODES = {"4", "7", "9", "10", "11", "15"}
_PHILLY_IND_CODES  = {"5"}
_PHILLY_VAC_CODES  = {"6", "12"}


def _philly_codes_for(property_classes):
    types = set(property_classes or ["Commercial", "Industrial", "Vacant"])
    codes = set()
    if "Commercial" in types:
        codes |= _PHILLY_COMM_CODES
    if "Industrial" in types:
        codes |= _PHILLY_IND_CODES
    if "Vacant" in types:
        codes |= _PHILLY_VAC_CODES
    return codes


def _philly_prop_class(cat):
    if cat in _PHILLY_VAC_CODES:
        return "Vacant"
    if cat in _PHILLY_IND_CODES:
        return "Industrial"
    return "Commercial"


def _fetch_philadelphia(property_classes, max_value, min_acres, max_acres):
    codes = _philly_codes_for(property_classes)
    if not codes:
        return []

    code_list = ",".join(f"'{c}'" for c in sorted(codes))
    min_sqft   = min_acres * 43560
    max_sqft   = max_acres * 43560

    where = (
        f"category_code IN ({code_list})"
        f" AND market_value > 0 AND market_value <= {max_value}"
        f" AND total_area >= {min_sqft} AND total_area <= {max_sqft}"
    )

    rows   = []
    offset = 0
    while True:
        params = {
            "where":             where,
            "outFields":         (
                "parcel_number,location,owner_1,owner_2,"
                "mailing_address_1,mailing_city_state,mailing_zip,"
                "market_value,total_area,zip_code,category_code"
            ),
            "returnGeometry":    "true",
            "resultOffset":      offset,
            "resultRecordCount": PAGE_SIZE,
            "orderByFields":     "objectid",
            "outSR":             "4326",
            "f":                 "json",
        }
        try:
            resp = requests.get(
                f"{PHILLY_URL}/query", params=params,
                headers=HEADERS, timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            raise ValueError(f"Philadelphia OPA query failed: {exc}") from exc

        if "error" in data:
            raise ValueError(f"Philadelphia OPA error: {data['error'].get('message', data['error'])}")

        features = data.get("features", [])
        for f in features:
            a = f["attributes"]
            g = f.get("geometry") or {}
            cat = str(a.get("category_code") or "").strip()
            sqft = float(a.get("total_area") or 0)
            acres = round(sqft / 43560, 4)

            cs = str(a.get("mailing_city_state") or "").strip()
            cs_parts = cs.rsplit(" ", 1)  # "CITY ST" split on last space
            owner_state = cs_parts[-1].strip() if len(cs_parts) > 1 else ""

            rows.append({
                "parcel_id":      str(a.get("parcel_number") or "").strip(),
                "address":        str(a.get("location") or "").strip(),
                "city":           "Philadelphia",
                "zip":            str(a.get("zip_code") or "").strip(),
                "property_class": _philly_prop_class(cat),
                "land_sqft":      round(sqft, 1),
                "land_acres":     acres,
                "assessed_value": float(a.get("market_value") or 0),
                "owner_name":     str(a.get("owner_1") or "").strip(),
                "owner_address":  str(a.get("mailing_address_1") or "").strip(),
                "owner_city":     cs_parts[0].strip() if cs_parts else "",
                "owner_state":    owner_state,
                "owner_zip":      str(a.get("mailing_zip") or "").strip(),
                "lat":            g.get("y"),
                "lng":            g.get("x"),
                "out_of_state":   owner_state.upper() not in ("PA", "PENNSYLVANIA", ""),
                "county":         "Philadelphia County",
                "luc_msg":        f"OPA cat {cat}",
            })

        if not data.get("exceededTransferLimit", False):
            break
        offset += len(features)
        time.sleep(0.5)

    return rows


def _fetch_pittsburgh(property_classes, max_value, min_acres, max_acres):
    types   = set(property_classes or ["Commercial", "Industrial", "Vacant"])
    min_sqft = min_acres * 43560
    max_sqft = max_acres * 43560

    # Always fetch both CLASS=C and CLASS=I; post-filter by building value for Vacant
    where_class = "\"CLASS\" IN ('C', 'I')"
    where_val   = f"\"FAIRMARKETTOTAL\" > 0 AND \"FAIRMARKETTOTAL\" <= {max_value}"
    where_area  = f"\"LOTAREA\" >= {min_sqft} AND \"LOTAREA\" <= {max_sqft}"
    where_city  = "\"PROPERTYCITY\" = 'PITTSBURGH'"

    sql_base = (
        f'SELECT "PARID","PROPERTYHOUSENUM","PROPERTYADDRESS","PROPERTYCITY","PROPERTYZIP",'
        f'"CLASSDESC","LOTAREA","FAIRMARKETTOTAL","FAIRMARKETBUILDING",'
        f'"CHANGENOTICEADDRESS1","CHANGENOTICEADDRESS3","CHANGENOTICEADDRESS4" '
        f'FROM "{ASSESS_RID}" '
        f'WHERE {where_class} AND {where_city} AND {where_val} AND {where_area}'
    )

    rows   = []
    offset = 0
    while True:
        sql = f"{sql_base} ORDER BY \"PARID\" LIMIT 2000 OFFSET {offset}"
        try:
            resp = requests.get(CKAN_SQL_URL, params={"sql": sql}, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            raise ValueError(f"Pittsburgh CKAN query failed: {exc}") from exc

        if not data.get("success"):
            err = data.get("error", {})
            raise ValueError(f"Pittsburgh CKAN error: {err}")

        records = data["result"]["records"]
        if not records:
            break

        rows.extend(records)
        if len(records) < 2000:
            break
        offset += 2000
        time.sleep(0.3)

    if not rows:
        return []

    # Determine property class
    def _pgh_class(rec):
        bldg = rec.get("FAIRMARKETBUILDING") or 0
        if bldg <= 0:
            return "Vacant"
        if str(rec.get("CLASSDESC") or "").upper() == "INDUSTRIAL":
            return "Industrial"
        return "Commercial"

    # Filter to requested property classes
    for rec in rows:
        rec["_prop_class"] = _pgh_class(rec)
    rows = [r for r in rows if r["_prop_class"] in types]

    if not rows:
        return []

    # Batch-fetch centroids
    parids = [r["PARID"] for r in rows]
    centroid_map = {}
    BATCH = 100
    for i in range(0, len(parids), BATCH):
        chunk = parids[i : i + BATCH]
        pin_list = "', '".join(chunk)
        sql_c = (
            f'SELECT "PIN", "LAT", "LONG" FROM "{CENTROID_RID}" '
            f"WHERE \"PIN\" IN ('{pin_list}')"
        )
        try:
            resp = requests.get(CKAN_SQL_URL, params={"sql": sql_c}, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            d = resp.json()
            if d.get("success"):
                for crec in d["result"]["records"]:
                    centroid_map[crec["PIN"]] = (crec["LAT"], crec["LONG"])
        except Exception:
            pass
        time.sleep(0.2)

    # Build normalized rows
    result = []
    for rec in rows:
        parid = rec["PARID"]
        lat, lng = centroid_map.get(parid, (None, None))
        if lat is None or lng is None:
            continue

        addr_num  = str(rec.get("PROPERTYHOUSENUM") or "").strip()
        addr_str  = str(rec.get("PROPERTYADDRESS") or "").strip()
        address   = f"{addr_num} {addr_str}".strip()

        raw_addr1 = str(rec.get("CHANGENOTICEADDRESS1") or "").strip()
        raw_cs    = str(rec.get("CHANGENOTICEADDRESS3") or "").strip()
        raw_zip   = str(rec.get("CHANGENOTICEADDRESS4") or "").strip().lstrip("0").zfill(5)

        cs_parts     = raw_cs.rsplit(" ", 1)
        owner_state  = cs_parts[-1].strip() if len(cs_parts) > 1 else ""
        owner_city   = cs_parts[0].strip() if len(cs_parts) > 1 else raw_cs.strip()

        sqft  = float(rec.get("LOTAREA") or 0)
        acres = round(sqft / 43560, 4)

        result.append({
            "parcel_id":      parid,
            "address":        address,
            "city":           str(rec.get("PROPERTYCITY") or "").strip().title(),
            "zip":            str(rec.get("PROPERTYZIP") or "").strip(),
            "property_class": rec["_prop_class"],
            "land_sqft":      round(sqft, 1),
            "land_acres":     acres,
            "assessed_value": float(rec.get("FAIRMARKETTOTAL") or 0),
            "owner_name":     "",
            "owner_address":  raw_addr1,
            "owner_city":     owner_city,
            "owner_state":    owner_state,
            "owner_zip":      raw_zip,
            "lat":            float(lat),
            "lng":            float(lng),
            "out_of_state":   owner_state.upper() not in ("PA", "PENNSYLVANIA", ""),
            "county":         "Allegheny County",
            "luc_msg":        str(rec.get("CLASSDESC") or "").title(),
        })

    return result


def fetch_parcels(city_cfg, property_classes, max_value, min_acres, max_acres):
    city_key = city_cfg.get("pa_city", "philadelphia")

    if city_key == "philadelphia":
        rows = _fetch_philadelphia(property_classes, max_value, min_acres, max_acres)
    elif city_key == "pittsburgh":
        rows = _fetch_pittsburgh(property_classes, max_value, min_acres, max_acres)
    else:
        rows = []

    if not rows:
        return pd.DataFrame(columns=[
            "parcel_id", "address", "city", "zip", "property_class",
            "land_sqft", "land_acres", "assessed_value",
            "owner_name", "owner_address", "owner_city", "owner_state", "owner_zip",
            "lat", "lng", "out_of_state", "county", "luc_msg",
        ])

    df = pd.DataFrame(rows)
    df = df.dropna(subset=["lat", "lng"]).drop_duplicates(subset=["parcel_id"]).reset_index(drop=True)
    return df
