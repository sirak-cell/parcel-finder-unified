"""
probe_tn.py — run LOCALLY (these county GIS servers IP-block datacenters, so this
won't work from a cloud sandbox — only from your machine).

Dumps each Tennessee county's parcel endpoint: field names, types, a sample feature,
and a class-code sample. Use the output to write verified fetcher adapters.

Usage:
    python probe_tn.py                # all four
    python probe_tn.py nashville      # just one
"""

import json
import sys
import urllib.parse
import urllib.request

HEADERS = {"User-Agent": "ParcelFinderBot/1.0 (internal drone-hub research)"}
TIMEOUT = 30

# Only Nashville is confirmed live. The other three are best-guess starting points.
# If a candidate 404s, browse the parent /rest/services dir, find the Parcels layer,
# and paste its URL here (the layer URL ending in /MapServer/0 or /FeatureServer/0).
CANDIDATES = {
    "nashville": [
        "https://services2.arcgis.com/HdTo6HJqh92wn4D8/arcgis/rest/services/Parcels/FeatureServer/0",
        "https://maps.nashville.gov/arcgis/rest/services/Cadastral/Cadastral_Layers/MapServer/0",
    ],
    "memphis": [   # Shelby County — gis.shelbycountytn.gov / register.shelby.tn.us
        "https://gis.shelbycountytn.gov/arcgis/rest/services/Parcels/MapServer/0",
    ],
    "knoxville": [  # Knox County — KGIS (www.kgis.org/arcgis)
        "https://www.kgis.org/arcgis/rest/services/Parcels/MapServer/0",
    ],
    "chattanooga": [  # Hamilton County TN / Chattanooga — gis.hamiltontn.gov / maps.chattanooga.gov
        "https://www.gisportal.hamiltontn.gov/arcgis/rest/services/Parcels/MapServer/0",
    ],
}


def _get(url, params):
    q = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{url}?{q}", headers=HEADERS)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def dump(county):
    print(f"\n{'='*70}\n{county.upper()}\n{'='*70}")
    for url in CANDIDATES[county]:
        print(f"\n-> trying {url}")
        try:
            meta = _get(url, {"f": "json"})
        except Exception as e:
            print(f"   UNREACHABLE: {e}")
            continue
        if "error" in meta:
            print(f"   API error: {meta['error']}")
            continue

        print(f"   OK  layer: {meta.get('name')!r}  geom: {meta.get('geometryType')}  "
              f"maxRecordCount: {meta.get('maxRecordCount')}")
        print(f"   {len(meta.get('fields', []))} fields:")
        for f in meta.get("fields", []):
            print(f"      {f['name']:<28} {f['type']:<22} {f.get('alias','')}")

        try:
            sample = _get(url + "/query", {
                "where": "1=1", "outFields": "*",
                "resultRecordCount": 1, "returnGeometry": "false", "f": "json",
            })
            feats = sample.get("features", [])
            if feats:
                print("\n   SAMPLE FEATURE attributes:")
                for k, v in feats[0]["attributes"].items():
                    print(f"      {k:<28} = {v!r}")
        except Exception as e:
            print(f"   sample query failed: {e}")

        print(f"\n   >>> WORKING ENDPOINT for {county}: {url}")
        return

    print(f"   !! No candidate worked for {county}. "
          f"Browse the parent /rest/services dir and find the parcel layer.")


if __name__ == "__main__":
    targets = sys.argv[1:] or list(CANDIDATES)
    for c in targets:
        if c not in CANDIDATES:
            print(f"unknown county: {c} (choose from {list(CANDIDATES)})")
            continue
        dump(c)
