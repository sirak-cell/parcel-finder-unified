"""
Tennessee parcel fetcher — Nashville/Davidson County Cadastral MapServer.

Endpoint: maps.nashville.gov/arcgis/rest/services/Cadastral/Parcels/MapServer/0
Centroid:  polygon ring average (server does not support returnCentroid)

Normalized output schema:
  parcel_id, address, city, zip, property_class, land_sqft, land_acres,
  assessed_value, owner_name, owner_address, owner_city, owner_state,
  owner_zip, lat, lng, out_of_state, county, luc_msg
"""

import json
import time
import urllib.parse
import urllib.request

import pandas as pd

NASHVILLE_URL = (
    "https://maps.nashville.gov/arcgis/rest/services"
    "/Cadastral/Parcels/MapServer/0"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}
PAGE_SIZE = 2000

# Nashville/Davidson County LUCode classification
# Commercial built: retail, office, auto, restaurant, recreation, warehousing
_COMM_CODES = [
    "021","022","023","024","025","026","027","028","029",
    "031","032","033","034","035","036",
    "041","042","043","044","045","046","047","048","049",
    "051","052","053","054","055","056","057","058","059",
    "061","063","064","065","066","067","068","069",
    "075",
]
# Industrial built: light/heavy manufacturing, warehousing, processing
_IND_CODES = ["071","072","073","074","076","077","078"]
# Vacant land: vacant commercial, industrial, rural
_VAC_CODES = ["020", "070", "080", "80M"]

_GOV_TERMS = frozenset([
    "METRO GOVERNMENT", "METROPOLITAN GOVERNMENT", "DAVIDSON COUNTY",
    "STATE OF TENNESSEE", "UNITED STATES", "U.S. GOVERNMENT",
    "METRO NASHVILLE", "CITY OF NASHVILLE",
])


def _ring_centroid(geometry):
    rings = (geometry or {}).get("rings", [])
    if not rings or not rings[0]:
        return None, None
    pts = rings[0]
    return (
        sum(p[1] for p in pts) / len(pts),
        sum(p[0] for p in pts) / len(pts),
    )


def _build_where(max_value, min_acres, max_acres, property_classes):
    types = property_classes or ["Commercial", "Industrial", "Vacant"]
    exprs = []

    if "Commercial" in types:
        codes = "','".join(_COMM_CODES)
        exprs.append(f"(LUCode IN ('{codes}'))")

    if "Industrial" in types:
        codes = "','".join(_IND_CODES)
        exprs.append(f"(LUCode IN ('{codes}'))")

    if "Vacant" in types:
        codes = "','".join(_VAC_CODES)
        exprs.append(f"(LUCode IN ('{codes}') AND ImprAppr = 0)")

    if not exprs:
        return "1=0"

    prop_expr = "(" + " OR ".join(exprs) + ")"
    return (
        f"{prop_expr}"
        f" AND IsActive = 'Y'"
        f" AND TotlAppr > 0 AND TotlAppr <= {max_value}"
        f" AND Acres >= {min_acres} AND Acres <= {max_acres}"
    )


def _classify(lu_code):
    if lu_code in _VAC_CODES:
        return "Vacant"
    if lu_code in _IND_CODES:
        return "Industrial"
    return "Commercial"


def _post(params, timeout=120):
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(
        f"{NASHVILLE_URL}/query", data=data,
        headers={**HEADERS, "Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except Exception:
            if attempt == 4:
                raise
            time.sleep(2 ** attempt)


def fetch_parcels(city_cfg, property_classes, max_value, min_acres, max_acres):
    where = _build_where(max_value, min_acres, max_acres, property_classes)
    records = []
    offset = 0

    while True:
        d = _post({
            "where": where,
            "outFields": (
                "APN,PropAddr,PropCity,PropZip,"
                "LUCode,LUDesc,Acres,"
                "TotlAppr,LandAppr,ImprAppr,"
                "Owner,OwnAddr1,OwnCity,OwnState,OwnZip"
            ),
            "returnGeometry": "true",
            "outSR": "4326",
            "resultOffset": offset,
            "resultRecordCount": PAGE_SIZE,
            "orderByFields": "OBJECTID",
            "f": "json",
        })

        feats = d.get("features", [])
        if not feats:
            break

        for feat in feats:
            a = feat["attributes"]
            lat, lng = _ring_centroid(feat.get("geometry"))
            if lat is None or lng is None:
                continue

            owner = str(a.get("Owner") or "").strip()
            if any(t in owner.upper() for t in _GOV_TERMS):
                continue

            lu_code    = str(a.get("LUCode") or "").strip()
            acres      = float(a.get("Acres") or 0)
            owner_state = str(a.get("OwnState") or "").strip()

            records.append({
                "parcel_id":      str(a.get("APN") or "").strip(),
                "address":        str(a.get("PropAddr") or "").strip(),
                "city":           str(a.get("PropCity") or "").strip(),
                "zip":            str(a.get("PropZip") or "").strip(),
                "property_class": _classify(lu_code),
                "land_sqft":      round(acres * 43560, 1),
                "land_acres":     round(acres, 4),
                "assessed_value": float(a.get("TotlAppr") or 0),
                "owner_name":     owner,
                "owner_address":  str(a.get("OwnAddr1") or "").strip(),
                "owner_city":     str(a.get("OwnCity") or "").strip(),
                "owner_state":    owner_state,
                "owner_zip":      str(a.get("OwnZip") or "").strip(),
                "lat":            lat,
                "lng":            lng,
                "out_of_state":   owner_state.upper() not in ("TN", "TENNESSEE", ""),
                "county":         "Davidson County",
                "luc_msg":        str(a.get("LUDesc") or lu_code).strip(),
            })

        offset += len(feats)
        if len(feats) < PAGE_SIZE and not d.get("exceededTransferLimit", False):
            break
        time.sleep(0.25)

    if not records:
        return pd.DataFrame(columns=[
            "parcel_id", "address", "city", "zip", "property_class",
            "land_sqft", "land_acres", "assessed_value",
            "owner_name", "owner_address", "owner_city", "owner_state", "owner_zip",
            "lat", "lng", "out_of_state", "county", "luc_msg",
        ])

    df = pd.DataFrame(records)
    df = df.dropna(subset=["lat", "lng"]).drop_duplicates(subset=["parcel_id"])
    return df.reset_index(drop=True)
