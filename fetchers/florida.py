"""
Florida parcel fetcher — Florida Statewide Cadastral FeatureServer/0.

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

BASE_URL = (
    "https://services9.arcgis.com/Gh9awoU677aKree0/arcgis/rest/services"
    "/Florida_Statewide_Cadastral/FeatureServer/0"
)
HEADERS = {"User-Agent": "ParcelFinderBot/1.0"}
MAX_REC = 2000

COMM_CODES = [f"{i:03d}" for i in range(11, 40)]
IND_CODES  = [f"{i:03d}" for i in range(41, 50)]
VAC_CODES  = ["010", "040"]

DOR_LABELS = {
    "010": "Vacant Commercial", "011": "Stores (1-story)", "012": "Mixed Use",
    "013": "Department Stores", "014": "Supermarkets", "015": "Regional Shopping",
    "016": "Community Shopping", "017": "Office (non-prof)", "018": "Office (prof)",
    "019": "Professional Services", "020": "Airports/Terminals", "021": "Restaurants",
    "022": "Drive-in Restaurants", "023": "Financial Institutions", "024": "Insurance",
    "025": "Repair Shops", "026": "Service Stations", "027": "Auto Sales/Repair",
    "028": "Parking Lots", "029": "Wholesale/Mfg Outlets", "030": "Florists",
    "031": "Drive-in Theaters", "032": "Theaters/Auditoriums", "033": "Bars",
    "034": "Bowling/Skating", "035": "Tourist Attractions", "036": "Camps",
    "037": "Race Tracks", "038": "Golf Courses", "039": "Hotels/Motels",
    "040": "Vacant Industrial", "041": "Light Manufacturing", "042": "Heavy Industrial",
    "043": "Lumber Yards", "044": "Open Storage",
    "045": "Mineral Processing", "046": "Warehouses/Distribution", "047": "Public Utility",
    "048": "Mining/Extraction", "049": "Nurseries/Orchards (Industrial)",
}

OUTFIELDS = ",".join([
    "PARCEL_ID", "PHY_ADDR1", "PHY_CITY", "PHY_ZIPCD",
    "DOR_UC", "JV", "LND_VAL", "LND_SQFOOT",
    "OWN_NAME", "OWN_ADDR1", "OWN_CITY", "OWN_STATE", "OWN_ZIPCD",
    "SALE_PRC1", "SALE_YR1",
])


def _post(params, timeout=90):
    data = urllib.parse.urlencode(params).encode()
    for attempt in range(5):
        try:
            req = urllib.request.Request(
                f"{BASE_URL}/query", data=data,
                headers={**HEADERS, "Content-Type": "application/x-www-form-urlencoded"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except Exception:
            if attempt == 4:
                raise
            time.sleep(2 ** attempt)


def _dor_to_class(dor):
    if dor in VAC_CODES:
        return "Vacant"
    if dor in IND_CODES:
        return "Industrial"
    return "Commercial"


def fetch_parcels(city_cfg, property_classes, max_value, min_acres, max_acres):
    co_no = city_cfg["co_no"]
    classes = property_classes or ["Commercial", "Industrial", "Vacant"]

    selected_codes = []
    if "Commercial" in classes:
        selected_codes += COMM_CODES
    if "Industrial" in classes:
        selected_codes += IND_CODES
    if "Vacant" in classes:
        selected_codes += VAC_CODES
    selected_codes = sorted(set(selected_codes))

    if not selected_codes:
        return pd.DataFrame()

    codes_str = "','".join(selected_codes)
    min_sqft = int(min_acres * 43560)
    max_sqft = int(max_acres * 43560)
    # LND_SQFOOT excluded from server-side WHERE — many valid FDOR commercial
    # parcels have LND_SQFOOT=0 even when they have real land area, causing 0
    # results. Filter client-side instead.
    where = (
        f"CO_NO={co_no}"
        f" AND DOR_UC IN ('{codes_str}')"
        f" AND JV > 0 AND JV <= {max_value}"
    )

    records = []
    offset = 0
    while True:
        d = _post({
            "where": where, "outFields": OUTFIELDS,
            "returnCentroid": "true", "returnGeometry": "false",
            "outSR": "4326", "resultOffset": offset,
            "resultRecordCount": MAX_REC, "f": "json",
        }, timeout=120)
        feats = d.get("features", [])
        if not feats:
            break
        for feat in feats:
            a = feat["attributes"]
            c = feat.get("centroid") or {}
            lat, lng = c.get("y"), c.get("x")
            if lat is None or lng is None:
                continue
            sqft = int(a.get("LND_SQFOOT") or 0)
            if sqft == 0:
                continue  # no usable size data
            if sqft < min_sqft or sqft > max_sqft:
                continue
            acres = sqft / 43560
            dor = str(a.get("DOR_UC") or "").strip()
            # For Vacant DOR codes, skip parcels where improvement value > $5k
            if dor in VAC_CODES:
                lnd_val = float(a.get("LND_VAL") or 0)
                jv      = float(a.get("JV") or 0)
                if jv - lnd_val > 5000:
                    continue
            owner_state = str(a.get("OWN_STATE") or "").strip()
            try:
                zip_str = str(int(a.get("PHY_ZIPCD") or 0))
            except (ValueError, TypeError):
                zip_str = str(a.get("PHY_ZIPCD") or "")
            try:
                own_zip = str(int(a.get("OWN_ZIPCD") or 0))
            except (ValueError, TypeError):
                own_zip = str(a.get("OWN_ZIPCD") or "")

            records.append({
                "parcel_id":      str(a.get("PARCEL_ID") or "").strip(),
                "address":        str(a.get("PHY_ADDR1") or "").strip(),
                "city":           str(a.get("PHY_CITY") or "").strip(),
                "zip":            zip_str,
                "property_class": _dor_to_class(dor),
                "land_sqft":      float(sqft),
                "land_acres":     round(acres, 4),
                "assessed_value": float(a.get("JV") or 0),
                "owner_name":     str(a.get("OWN_NAME") or "").strip(),
                "owner_address":  str(a.get("OWN_ADDR1") or "").strip(),
                "owner_city":     str(a.get("OWN_CITY") or "").strip(),
                "owner_state":    owner_state,
                "owner_zip":      own_zip,
                "lat":            lat,
                "lng":            lng,
                "out_of_state":   owner_state.upper() not in ("FL", "FLORIDA", ""),
                "county":         "Florida",
                "luc_msg":        DOR_LABELS.get(dor, dor),
            })
        offset += len(feats)
        if len(feats) < MAX_REC:
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
