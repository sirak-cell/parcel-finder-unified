import json

import folium
from folium.plugins import Fullscreen
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

from config import DEFAULTS, MARKETS
from fetcher import fetch_parcels, fetch_walmarts
from geo import filter_by_walmart_proximity

st.set_page_config(page_title="Parcel Finder — Unified", layout="wide")
st.title("Parcel Finder — Drone Hub Site Sourcing")
st.caption("Utah · New Mexico · Colorado · Arizona · North Carolina · Georgia · Ohio · Florida — Commercial / Industrial / Vacant")

# ── Load Zipline profile ───────────────────────────────────────────────────────
@st.cache_data
def _load_zipline_profile():
    with open("zipline_profile.json") as f:
        return json.load(f)

ZIPLINE_PROFILE = _load_zipline_profile()


def _is_zipline_match(row, profile):
    sqft  = float(row.get("land_sqft") or 0)
    wm_mi = row.get("nearest_walmart_mi")
    pc    = row.get("property_class", "")
    sqft_ok  = profile["sqft_min"] <= sqft <= profile["sqft_max"]
    wm_ok    = (wm_mi is not None) and (wm_mi <= profile["max_walmart_mi"])
    class_ok = pc in profile["property_classes"]
    return sqft_ok and wm_ok and class_ok


# ── Sidebar ────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("Search Filters")

    state = st.selectbox("State", options=list(MARKETS.keys()))
    state_cfg = MARKETS[state]
    city_options = list(state_cfg["cities"].keys())
    city = st.selectbox("City / Area", options=city_options)
    city_cfg = state_cfg["cities"][city]

    prop_types = st.multiselect(
        "Property Type",
        options=["Commercial", "Industrial", "Vacant"],
        default=["Commercial", "Industrial"],
    )

    max_value = st.slider(
        "Max Assessed Value ($)", 0, 2_000_000,
        DEFAULTS["max_value"], step=25_000,
    )
    min_acres = st.number_input(
        "Min Size (acres)", min_value=0.01, max_value=10.0,
        value=DEFAULTS["min_acres"], format="%.4f", step=0.05,
    )
    max_acres = st.number_input(
        "Max Size (acres)", min_value=0.05, max_value=10.0,
        value=DEFAULTS["max_acres"], format="%.4f", step=0.05,
    )
    min_mi = st.slider("Min Walmart Distance (mi)", 0.0, 10.0, DEFAULTS["min_mi"], step=0.5)
    max_mi = st.slider("Max Walmart Distance (mi)", 0.5, 2.0, DEFAULTS["max_mi"], step=0.25)

    if min_acres >= max_acres:
        st.error("Min size must be less than max size.")
        st.stop()
    if min_mi >= max_mi:
        st.error("Min Walmart distance must be less than max.")
        st.stop()

    run = st.button("Run Search", type="primary", use_container_width=True)


# ── Session state ──────────────────────────────────────────────────────────────
if "results" not in st.session_state:
    st.session_state.results  = None
    st.session_state.walmarts = []
    st.session_state.city_cfg = None


# ── Run pipeline ───────────────────────────────────────────────────────────────
if run:
    with st.spinner(f"Fetching parcels from {state} — {city}…"):
        parcels = fetch_parcels(state, city, prop_types, max_value, min_acres, max_acres)
    st.info(f"Fetched **{len(parcels)}** parcels matching value + size filters.")

    with st.spinner("Fetching Walmart Supercenters…"):
        walmarts = fetch_walmarts(city_cfg)

    if not walmarts:
        st.warning("Overpass unavailable — Walmart proximity filter skipped.")
        results = parcels.copy()
        results["nearest_walmart_mi"]   = None
        results["nearest_walmart_name"] = "N/A"
    else:
        with st.spinner("Calculating Walmart proximity…"):
            results = filter_by_walmart_proximity(parcels, walmarts, min_mi, max_mi)
        if results.empty and not parcels.empty:
            st.warning("No parcels within that Walmart distance range. Try widening the distance filter.")

    # Government owner filter
    if not results.empty and "owner_name" in results.columns:
        _gov_re = (
            r"CITY OF|COUNTY OF|STATE OF|UNITED STATES|US GOVERNMENT"
            r"|DEPT OF|DEPARTMENT OF|AUTHORITY|TRANSIT|SCHOOL DIST|ISD\b"
        )
        before = len(results)
        results = results[~results["owner_name"].str.upper().str.contains(_gov_re, na=False, regex=True)].copy()
        removed = before - len(results)
        if removed:
            st.caption(f"Removed {removed} government-owned parcels.")

    st.session_state.results  = results
    st.session_state.walmarts = walmarts
    st.session_state.city_cfg = city_cfg


# ── Display ────────────────────────────────────────────────────────────────────
if st.session_state.results is not None:
    results  = st.session_state.results.copy()
    walmarts = st.session_state.walmarts
    _city_cfg = st.session_state.city_cfg or city_cfg

    # Search filter
    search = st.text_input(
        "Search results",
        placeholder="Address, owner name, or parcel ID…",
        help="Filters current results without re-running the pipeline.",
    )
    if search:
        mask = pd.Series(False, index=results.index)
        for col in ["address", "city", "owner_name", "parcel_id"]:
            if col in results.columns:
                mask |= results[col].astype(str).str.contains(search, case=False, na=False)
        results = results[mask].copy()

    commercial_results = results[results["property_class"] == "Commercial"].copy()
    industrial_results = results[results["property_class"] == "Industrial"].copy()
    vac_results        = results[results["property_class"] == "Vacant"].copy()
    ci_results = pd.concat([commercial_results, industrial_results], ignore_index=True)

    # Pre-count Zipline matches for summary (loop runs again during map rendering)
    zipline_count = sum(
        1 for _, r in results.iterrows() if _is_zipline_match(r, ZIPLINE_PROFILE)
    )

    st.success(
        f"**{len(results)} parcels** — "
        f"🟢 {zipline_count} Zipline-match · "
        f"🔵 {len(commercial_results)} Commercial · "
        f"⚫ {len(industrial_results)} Industrial · "
        f"🟠 {len(vac_results)} Vacant"
    )
    if zipline_count > 0:
        st.caption(
            f"🟢 Green = matches Zipline site profile "
            f"({int(ZIPLINE_PROFILE['sqft_min']):,}–{int(ZIPLINE_PROFILE['sqft_max']):,} sqft, "
            f"≤{ZIPLINE_PROFILE['max_walmart_mi']} mi from Walmart)"
        )
    if search:
        st.caption(f"Filter '{search}' — showing {len(results)} of {len(st.session_state.results)}")

    # ── Map ────────────────────────────────────────────────────────────────────
    map_center = (
        [results["lat"].mean(), results["lng"].mean()]
        if not results.empty
        else _city_cfg["map_center"]
    )
    m = folium.Map(location=map_center, zoom_start=11, tiles=None)
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri World Imagery",
        name="Satellite",
    ).add_to(m)
    folium.TileLayer(tiles="CartoDB positron", name="Street Map").add_to(m)
    folium.LayerControl(position="topright").add_to(m)
    Fullscreen(position="topleft", title="Full screen", title_cancel="Exit full screen", force_separate_button=True).add_to(m)

    # Walmart markers + 2-mile radius circles
    for w in walmarts:
        folium.Marker(
            [w["lat"], w["lng"]],
            icon=folium.Icon(color="red", icon="shopping-cart", prefix="fa"),
            popup=w["name"],
            tooltip=w["name"],
        ).add_to(m)
        folium.Circle(
            [w["lat"], w["lng"]],
            radius=2 * 1609.34,
            color="red",
            weight=1,
            fill=False,
            opacity=0.4,
        ).add_to(m)

    def _popup(row, prop_class):
        wm_mi   = row.get("nearest_walmart_mi")
        wm_name = row.get("nearest_walmart_name") or ""
        wm_str  = f"{wm_mi:.1f} mi ({wm_name})" if wm_mi is not None else "N/A"
        return (
            f"<b>{row.get('owner_name') or 'Unknown'}</b><br>"
            f"Mail: {row.get('owner_address','')} {row.get('owner_city','')} "
            f"{row.get('owner_state','')} {row.get('owner_zip','')}<br>"
            f"Situs: {row.get('address','')}, {row.get('city','')}<br>"
            f"Type: {prop_class} | Assessed: ${float(row.get('assessed_value') or 0):,.0f}<br>"
            f"Land: {float(row.get('land_acres') or 0):.3f} ac "
            f"({int(row.get('land_sqft') or 0):,} sq ft)<br>"
            f"Nearest Walmart: {wm_str}<br>"
            f"County: {row.get('county','')}<br>"
            f"Use: {row.get('luc_msg','')}<br>"
            f"Parcel: {row.get('parcel_id','')}"
        )

    # Parcel pins — green=Zipline match, blue=Commercial, gray=Industrial, orange=Vacant
    for _, r in results.iterrows():
        if r.get("lat") is None or r.get("lng") is None:
            continue
        pc = r.get("property_class", "Commercial")
        if _is_zipline_match(r, ZIPLINE_PROFILE):
            color = "green"
        elif pc == "Industrial":
            color = "gray"
        elif pc == "Vacant":
            color = "orange"
        else:
            color = "blue"
        folium.Marker(
            [r["lat"], r["lng"]],
            icon=folium.Icon(color=color),
            popup=folium.Popup(_popup(r, pc), max_width=360),
            tooltip=str(r.get("address", "")),
        ).add_to(m)

    st_folium(m, height=900, use_container_width=True)
    st.caption(
        "Map: 🟢 Zipline-match · 🔵 Commercial · ⚫ Industrial · 🟠 Vacant · 🔴 Walmart (2-mi ring)"
    )

    # ── Summary & tables ───────────────────────────────────────────────────────
    DISPLAY_COLS = [
        "parcel_id", "address", "city", "zip",
        "property_class", "luc_msg",
        "land_sqft", "land_acres", "assessed_value",
        "nearest_walmart_mi", "nearest_walmart_name",
        "owner_name", "owner_address", "owner_city", "owner_state", "owner_zip",
        "out_of_state", "county",
    ]

    def _safe_cols(df):
        return [c for c in DISPLAY_COLS if c in df.columns]

    ts = pd.Timestamp.now().strftime("%Y%m%d_%H%M")
    slug = f"{state.lower().replace(' ', '_')}_{city.lower().replace(' ', '_').replace('/', '_')}"

    # ── Commercial ────────────────────────────────────────────────────────────
    st.subheader(f"🔵 Commercial ({len(commercial_results)})")
    if commercial_results.empty:
        st.info("No Commercial parcels matched all filters.")
    else:
        st.dataframe(commercial_results[_safe_cols(commercial_results)], use_container_width=True)
        st.download_button(
            "⬇️ Download Commercial CSV",
            data=commercial_results[_safe_cols(commercial_results)].to_csv(index=False),
            file_name=f"{slug}_commercial_{ts}.csv",
            mime="text/csv",
            key="dl_commercial",
        )

    # ── Industrial ────────────────────────────────────────────────────────────
    st.subheader(f"⚫ Industrial ({len(industrial_results)})")
    if industrial_results.empty:
        st.info("No Industrial parcels matched all filters.")
    else:
        st.dataframe(industrial_results[_safe_cols(industrial_results)], use_container_width=True)
        st.download_button(
            "⬇️ Download Industrial CSV",
            data=industrial_results[_safe_cols(industrial_results)].to_csv(index=False),
            file_name=f"{slug}_industrial_{ts}.csv",
            mime="text/csv",
            key="dl_industrial",
        )

    # ── Vacant ────────────────────────────────────────────────────────────────
    st.subheader(f"🟠 Vacant ({len(vac_results)})")
    if vac_results.empty:
        st.info("No Vacant parcels matched all filters.")
    else:
        st.caption("Verify zoning before pursuing Vacant parcels.")
        st.dataframe(vac_results[_safe_cols(vac_results)], use_container_width=True)
        st.download_button(
            "⬇️ Download Vacant CSV",
            data=vac_results[_safe_cols(vac_results)].to_csv(index=False),
            file_name=f"{slug}_vacant_{ts}.csv",
            mime="text/csv",
            key="dl_vac",
        )

    # ── Zipline-match combined CSV ─────────────────────────────────────────────
    if zipline_count > 0:
        zipline_matches = results[results.apply(
            lambda r: _is_zipline_match(r, ZIPLINE_PROFILE), axis=1
        )].copy()
        st.download_button(
            f"⬇️ Download Zipline-Match CSV ({zipline_count} parcels)",
            data=zipline_matches[_safe_cols(zipline_matches)].to_csv(index=False),
            file_name=f"{slug}_zipline_match_{ts}.csv",
            mime="text/csv",
            key="dl_zipline",
        )

else:
    st.info("Set filters in the sidebar and click **Run Search** to begin.")
