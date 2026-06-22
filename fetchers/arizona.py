"""
Arizona parcel fetcher — Maricopa County assessor (IndividualService/Parcel/MapServer/1).

Quirks:
  - Lat/lng embedded in attributes (Latitude_DD, Longitude_DD) — no geometry needed
  - LandLegalClassCode drives class filter: 1.8/1.10/1.12 = improved commercial/industrial
  - Vacant commercial/industrial: PropertyUseCode in (0021, 0022, 0031, 0032)
  - POST all queries (avoids URL length limit)
  - Owner data embedded in layer
"""

import json
import random
import time
import urllib.parse
import urllib.request

import pandas as pd

_ENDPOINT = (
    "https://gis.maricopa.gov/arcgis/rest/services"
    "/IndividualService/Parcel/MapServer/1/query"
)
_HEADERS = {"User-Agent": "ParcelFinderBot/1.0 (internal drone-hub research tool)"}
_PAGE_SIZE = 1000
_RETRY_CODES = {429, 503, 500}
_MAX_ATTEMPTS = 5

# LandLegalClassCode values for improved commercial/industrial parcels
_COMM_IND_CLASSES = ("'1.8'", "'1.10'", "'1.12'")

# PropertyUseCode values for vacant commercial and industrial land
_VACANT_CODES = ("'0021'", "'0022'", "'0031'", "'0032'")

_OUTFIELDS = ",".join([
    "APN",
    "PropertyFullStreetAddress",
    "PropertyCity",
    "PropertyZipCode",
    "LandLegalClassCode",
    "LandLegalClassDescription",
    "PropertyUseCode",
    "PropertyUseDescription",
    "FullCashValue",
    "LotSize_SqFt",
    "LotSize_Acre",
    "OwnerName",
    "OwnerAddressLine1",
    "OwnerCity",
    "OwnerState",
    "OwnerZipCode",
    "Latitude_DD",
    "Longitude_DD",
])


def _post_query(params):
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(
        _ENDPOINT, data=data,
        headers={**_HEADERS, "Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
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
            raise ValueError(f"Maricopa API error: {msg}")
        return data
    raise ValueError(f"Maricopa API throttled after {_MAX_ATTEMPTS} attempts")


def _build_where(city_filter, max_value, min_acres, max_acres, property_classes):
    if not property_classes:
        property_classes = ["Commercial", "Industrial", "Vacant"]

    class_parts = []
    if any(c in property_classes for c in ("Commercial", "Industrial")):
        codes = ", ".join(_COMM_IND_CLASSES)
        class_parts.append(f"LandLegalClassCode IN ({codes})")
    if "Vacant" in property_classes:
        vcodes = ", ".join(_VACANT_CODES)
        class_parts.append(f"PropertyUseCode IN ({vcodes})")

    if not class_parts:
        return "1=0"

    class_expr = f"({' OR '.join(class_parts)})" if len(class_parts) > 1 else class_parts[0]
    city_expr  = f"PropertyCity = '{city_filter}'"

    return (
        f"{city_expr}"
        f" AND {class_expr}"
        f" AND FullCashValue > 0 AND FullCashValue <= {max_value}"
        f" AND LotSize_Acre >= {min_acres} AND LotSize_Acre <= {max_acres}"
    )


def _classify(land_class_code, use_desc):
    uc = (use_desc or "").upper()
    if "VACANT" in uc and ("COMMERCIAL" in uc or "INDUSTRIAL" in uc):
        return "Vacant"
    # LandLegalClassCode 1.10 = "COMMERCIAL / MANUFACTURERS R/P"
    if land_class_code == "1.10":
        return "Industrial"
    if any(kw in uc for kw in ("WAREHOUSE", "INDUSTRIAL", "MANUFACTUR", "DISTRIBUTION", "FLEX")):
        return "Industrial"
    return "Commercial"


def fetch_parcels(city_cfg, property_classes, max_value, min_acres, max_acres):
    city_filter = city_cfg["city_filter"]
    where = _build_where(city_filter, max_value, min_acres, max_acres, property_classes)
    rows = []
    offset = 0

    while True:
        params = {
            "where": where,
            "outFields": _OUTFIELDS,
            "returnGeometry": "false",
            "resultOffset": offset,
            "resultRecordCount": _PAGE_SIZE,
            "orderByFields": "OBJECTID",
            "f": "json",
        }
        data = _fetch_page(params)
        features = data.get("features", [])

        for f in features:
            a = f["attributes"]

            lat = a.get("Latitude_DD")
            lng = a.get("Longitude_DD")
            if lat is None or lng is None:
                continue

            owner_state = str(a.get("OwnerState") or "").strip().upper()
            land_class  = str(a.get("LandLegalClassCode") or "").strip()
            use_desc    = str(a.get("PropertyUseDescription") or "").strip()
            acres       = float(a.get("LotSize_Acre") or 0)
            sqft        = float(a.get("LotSize_SqFt") or 0)
            # Prefer the explicit sqft field; fall back to acres conversion
            if sqft == 0 and acres > 0:
                sqft = round(acres * 43560, 1)

            prop_class = _classify(land_class, use_desc)
            # Server-side WHERE may return all three class types; filter to requested
            if prop_class not in property_classes:
                continue

            rows.append({
                "parcel_id":      str(a.get("APN") or "").strip(),
                "address":        str(a.get("PropertyFullStreetAddress") or "").strip(),
                "city":           str(a.get("PropertyCity") or "").strip().title(),
                "zip":            str(a.get("PropertyZipCode") or "").strip(),
                "property_class": prop_class,
                "land_sqft":      round(sqft, 1),
                "land_acres":     round(acres, 4),
                "assessed_value": float(a.get("FullCashValue") or 0),
                "owner_name":     str(a.get("OwnerName") or "").strip(),
                "owner_address":  str(a.get("OwnerAddressLine1") or "").strip(),
                "owner_city":     str(a.get("OwnerCity") or "").strip(),
                "owner_state":    owner_state,
                "owner_zip":      str(a.get("OwnerZipCode") or "").strip(),
                "lat":            float(lat),
                "lng":            float(lng),
                "out_of_state":   owner_state not in ("AZ", ""),
                "county":         "Maricopa County",
                "luc_msg":        use_desc,
            })

        if not data.get("exceededTransferLimit", False):
            break
        offset += len(features)
        time.sleep(0.3)

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
