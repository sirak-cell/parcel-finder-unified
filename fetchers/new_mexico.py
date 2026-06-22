"""
New Mexico parcel fetcher — Bernalillo County assessor MapServer/22.

Quirks:
  - IS NULL blocked server-side — use IMPTVALUE = 0
  - returnCentroid not supported — centroid from polygon ring average
  - POST all queries (avoids URL length limit)
  - Owner data embedded in layer
"""

import json
import random
import time
import urllib.parse
import urllib.request

import pandas as pd

BERNCO_ENDPOINT = "https://assessormap.bernco.gov/server/rest/services/GIS/ASROnline_Public_Map/MapServer/22"
HEADERS = {"User-Agent": "ParcelFinderBot/1.0 (internal drone-hub research tool)"}
PAGE_SIZE = 2000

INDUSTRIAL_LUCS = {"4396", "4397", "4398", "4399", "5401"}

_RETRY_CODES = {429, 503, 500}
_MAX_ATTEMPTS = 5

_GOV_TERMS = (
    "CITY OF ALBUQUERQUE", "COUNTY OF BERNALILLO", "STATE OF NEW MEXICO",
    "UNITED STATES", "U.S. GOVERNMENT", "BERNALILLO COUNTY",
    "ALBUQUERQUE PUBLIC SCHOOLS", "UNIVERSITY OF NEW MEXICO",
)


def _ring_centroid(geometry):
    rings = (geometry or {}).get("rings", [])
    if not rings or not rings[0]:
        return None, None
    pts = rings[0]
    return sum(p[1] for p in pts) / len(pts), sum(p[0] for p in pts) / len(pts)


def _post_query(params):
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(
        f"{BERNCO_ENDPOINT}/query", data=data,
        headers={**HEADERS, "Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _fetch_page(params):
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            data = _post_query(params)
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
            raise ValueError(f"Bernalillo API error: {msg}")
        return data
    raise ValueError(f"Bernalillo API throttled after {_MAX_ATTEMPTS} attempts")


def _build_where(max_value, min_acres, max_acres, property_classes):
    if not property_classes:
        property_classes = ["Commercial", "Industrial", "Vacant"]

    ind_lucs = "','".join(sorted(INDUSTRIAL_LUCS))
    exprs = []
    if "Commercial" in property_classes:
        exprs.append(f"(PROPCLASS = 'C' AND IMPTVALUE = 0 AND LUC NOT IN ('{ind_lucs}'))")
    if "Industrial" in property_classes:
        exprs.append(f"(PROPCLASS = 'C' AND IMPTVALUE = 0 AND LUC IN ('{ind_lucs}'))")
    if "Vacant" in property_classes:
        exprs.append("(PROPCLASS = 'V' AND LUC = '9300')")

    if not exprs:
        return "1=0"

    prop_expr = "(" + " OR ".join(exprs) + ")"
    gov_exclusion = (
        " AND LUC NOT IN ('7000','7610','7611','7612','7613',"
        "                  '7620','7630','7640','7660','7670','7680','7LFC')"
        " AND LUC NOT IN ('93CT','9700','97PK','9LFC','9555')"
    )
    return (
        f"{prop_expr}{gov_exclusion}"
        f" AND TOTVALUE > 0 AND TOTVALUE <= {max_value}"
        f" AND ACREAGE >= {min_acres} AND ACREAGE <= {max_acres}"
    )


def _classify(propclass, luc):
    if propclass == "V":
        return "Vacant"
    if luc in INDUSTRIAL_LUCS:
        return "Industrial"
    return "Commercial"


def fetch_parcels(city_cfg, property_classes, max_value, min_acres, max_acres):
    where = _build_where(max_value, min_acres, max_acres, property_classes)
    rows = []
    offset = 0

    while True:
        params = {
            "where": where,
            "outFields": (
                "UPC,SITUSADD,SITUSCITY,SITUSZIP,"
                "OWNER,OWNADD,OWNCITY,OWNSTATE,OWNZIPCODE,"
                "PROPCLASS,LUC,LUC_MSG,"
                "TOTVALUE,LANDVALUE,IMPTVALUE,ACREAGE"
            ),
            "returnGeometry": "true",
            "outSR": "4326",
            "resultOffset": offset,
            "resultRecordCount": PAGE_SIZE,
            "orderByFields": "OBJECTID",
            "f": "json",
        }
        data = _fetch_page(params)
        features = data.get("features", [])

        for f in features:
            a = f["attributes"]
            lat, lng = _ring_centroid(f.get("geometry"))

            owner = str(a.get("OWNER") or "").strip()
            if any(t in owner.upper() for t in _GOV_TERMS):
                continue

            propclass = str(a.get("PROPCLASS") or "").strip()
            luc       = str(a.get("LUC") or "").strip()
            acres     = float(a.get("ACREAGE") or 0)

            owner_city  = str(a.get("OWNCITY")    or "").strip()
            owner_state = str(a.get("OWNSTATE")   or "").strip()

            rows.append({
                "parcel_id":      str(a.get("UPC") or "").strip(),
                "address":        str(a.get("SITUSADD") or "").strip(),
                "city":           str(a.get("SITUSCITY") or "").strip(),
                "zip":            str(a.get("SITUSZIP") or "").strip(),
                "property_class": _classify(propclass, luc),
                "land_sqft":      round(acres * 43560, 1),
                "land_acres":     round(acres, 4),
                "assessed_value": float(a.get("TOTVALUE") or 0),
                "owner_name":     owner,
                "owner_address":  str(a.get("OWNADD") or "").strip(),
                "owner_city":     owner_city,
                "owner_state":    owner_state,
                "owner_zip":      str(a.get("OWNZIPCODE") or "").strip(),
                "lat":            lat,
                "lng":            lng,
                "out_of_state":   owner_state.upper() not in ("NM", "NEW MEXICO", ""),
                "county":         "Bernalillo County",
                "luc_msg":        str(a.get("LUC_MSG") or luc).strip(),
            })

        if not data.get("exceededTransferLimit", False):
            break
        offset += len(features)
        time.sleep(0.5)

    if not rows:
        return pd.DataFrame(columns=[
            "parcel_id", "address", "city", "zip", "property_class",
            "land_sqft", "land_acres", "assessed_value",
            "owner_name", "owner_address", "owner_city", "owner_state", "owner_zip",
            "lat", "lng", "out_of_state", "county", "luc_msg",
        ])

    df = pd.DataFrame(rows)
    df = df.dropna(subset=["lat", "lng"]).drop_duplicates(subset=["parcel_id"])
    return df.reset_index(drop=True)
