"""
Colorado parcel fetcher — gis.colorado.gov PropTaxMapParcels2024 MapServer/5.

Quirks:
  - a2024ActVal is esriFieldTypeString — value filter applied Python-side
  - returnCentroid not supported — centroid from polygon ring average
  - POST all queries (complex WHERE clause exceeds GET URL limit)
  - landSqft is Double; landAcres is NULL for commercial — derive from landSqft
"""

import json
import random
import time
import urllib.parse
import urllib.request

import pandas as pd

DENVER_ENDPOINT = "https://gis.colorado.gov/public/rest/services/GOV/PropTaxMapParcels2024/MapServer/5"
HEADERS = {"User-Agent": "ParcelFinderBot/1.0 (internal drone-hub research tool)"}
PAGE_SIZE = 2000

INDUSTRIAL_LUCS = {
    "441", "442", "443", "444", "445", "446", "447", "448",
    "471", "472", "473", "474",
    "551", "552", "553", "554", "555", "556",
    "571", "572", "573", "575",
    "44M", "47M",
}

_RETRY_CODES = {429, 503, 500}
_MAX_ATTEMPTS = 5

_GOV_TERMS = (
    "CITY AND COUNTY OF DENVER", "CITY OF DENVER", "STATE OF COLORADO",
    "UNITED STATES", "U.S. GOVERNMENT", "DENVER WATER",
    "DENVER PUBLIC SCHOOLS", "UNIVERSITY OF DENVER",
    "REGIONAL TRANSPORTATION", "RTD ",
    "URBAN DRAINAGE",
    "CITY & COUNTY OF DENVER",
)


def _ring_centroid(geometry):
    rings = (geometry or {}).get("rings", [])
    if not rings or not rings[0]:
        return None, None
    pts = rings[0]
    return sum(p[1] for p in pts) / len(pts), sum(p[0] for p in pts) / len(pts)


def _post_query(params, timeout=90):
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(
        f"{DENVER_ENDPOINT}/query", data=data,
        headers={**HEADERS, "Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
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
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt < _MAX_ATTEMPTS:
                time.sleep(2 ** attempt + random.uniform(0, 1))
                continue
            raise
        if "error" in data:
            msg = data["error"].get("message", str(data["error"]))
            code = data["error"].get("code", 0)
            if ("too many" in msg.lower() or code in _RETRY_CODES) and attempt < _MAX_ATTEMPTS:
                time.sleep(2 ** attempt + random.uniform(0, 1))
                continue
            raise ValueError(f"Colorado GIS API error: {msg}")
        return data
    raise ValueError(f"Colorado GIS API throttled after {_MAX_ATTEMPTS} attempts")


def _build_where(fips_codes, property_classes, min_acres, max_acres):
    fips_in = "','".join(fips_codes)
    county_expr = f"countyFips IN ('{fips_in}')"

    use_parts = []
    if "Commercial" in property_classes or not property_classes:
        use_parts.append("landUseDsc LIKE 'COMMERCIAL%'")
    if "Industrial" in property_classes or not property_classes:
        use_parts.append("landUseDsc LIKE 'INDUSTRIAL%'")
    if "Vacant" in property_classes or not property_classes:
        use_parts.append("landUseDsc LIKE 'VACANT LAND%'")

    use_expr = "(" + " OR ".join(use_parts) + ")" if use_parts else "1=1"

    exclusions = (
        " AND landUseDsc NOT LIKE '%CONDOMINIUM%'"
        " AND landUseDsc NOT LIKE '%RESIDENTIAL%'"
        " AND landUseDsc NOT LIKE '%APARTMENT%'"
        " AND landUseDsc NOT LIKE '%ROWHOUSE%'"
        " AND landUseDsc NOT LIKE '%DUPLEX%'"
        " AND landUseDsc NOT LIKE '%TRIPLEX%'"
        " AND landUseDsc NOT LIKE 'VACANT LAND /GENERAL COMMON%'"
        " AND landUseDsc NOT LIKE '%PARK%'"
        " AND landUseDsc NOT LIKE '%CEMETERY%'"
        " AND landUseDsc NOT LIKE '%SCHOOL%'"
        " AND landUseDsc NOT LIKE '%CHURCH%'"
        " AND landUseDsc NOT LIKE '%FIRE STATION%'"
    )
    min_sqft = min_acres * 43560
    max_sqft = max_acres * 43560
    return (
        f"{county_expr} AND {use_expr}{exclusions}"
        f" AND landSqft IS NOT NULL AND landSqft >= {min_sqft} AND landSqft <= {max_sqft}"
    )


def _classify(land_use_dsc, land_use_cde):
    dsc = (land_use_dsc or "").upper()
    if dsc.startswith("INDUSTRIAL") or land_use_cde in INDUSTRIAL_LUCS:
        return "Industrial"
    if dsc.startswith("VACANT"):
        return "Vacant"
    return "Commercial"


def fetch_parcels(city_cfg, property_classes, max_value, min_acres, max_acres):
    fips_codes = city_cfg.get("fips", ["031"])
    where = _build_where(fips_codes, property_classes or [], min_acres, max_acres)
    rows = []
    offset = 0

    while True:
        params = {
            "where": where,
            "outFields": (
                "parcel_id,situsAdd,sitAddCty,sitAddZip,"
                "owner,owner2,ownerAdd,ownAddCty,ownAddStt,ownAddZip,"
                "landUseCde,landUseDsc,"
                "landSqft,a2024ActVal,"
                "saleDate,salePrice"
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

            owner = str(a.get("owner") or "").strip()
            if any(t in owner.upper() for t in _GOV_TERMS):
                continue

            try:
                val = float(str(a.get("a2024ActVal") or "0").replace(",", "").replace("$", ""))
            except ValueError:
                val = 0.0
            if val <= 0 or val > max_value:
                continue

            sqft  = float(a.get("landSqft") or 0)
            acres = sqft / 43560
            luc   = str(a.get("landUseCde") or "").strip()
            dsc   = str(a.get("landUseDsc") or "").strip()
            owner_city  = str(a.get("ownAddCty") or "").strip()
            owner_state = str(a.get("ownAddStt") or "").strip()

            rows.append({
                "parcel_id":      str(a.get("parcel_id") or "").strip(),
                "address":        str(a.get("situsAdd") or "").strip(),
                "city":           str(a.get("sitAddCty") or "").strip(),
                "zip":            str(a.get("sitAddZip") or "").strip(),
                "property_class": _classify(dsc, luc),
                "land_sqft":      round(sqft, 1),
                "land_acres":     round(acres, 4),
                "assessed_value": val,
                "owner_name":     owner,
                "owner_address":  str(a.get("ownerAdd") or "").strip(),
                "owner_city":     owner_city,
                "owner_state":    owner_state,
                "owner_zip":      str(a.get("ownAddZip") or "").strip(),
                "lat":            lat,
                "lng":            lng,
                "out_of_state":   owner_state.upper() not in ("CO", "COLORADO", ""),
                "county":         "Colorado",
                "luc_msg":        dsc or luc,
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
