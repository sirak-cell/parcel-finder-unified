DEFAULTS = {
    "max_value": 500_000,
    "min_acres": 0.10,
    "max_acres": 0.50,
    "min_mi":    0.5,
    "max_mi":    2.0,
}

# ── Walmart static fallbacks (geocoded 2026-06-22) ────────────────────────────
_WALMART_DENVER = [
    {"name": "Walmart Supercenter - Federal Blvd",   "lat": 39.7721, "lng": -105.0192},
    {"name": "Walmart Supercenter - Hampden Ave",    "lat": 39.6487, "lng": -104.9808},
    {"name": "Walmart Supercenter - Peoria St",      "lat": 39.7156, "lng": -104.8372},
    {"name": "Walmart Supercenter - Sheridan Blvd",  "lat": 39.7451, "lng": -105.0519},
    {"name": "Walmart Supercenter - Wadsworth Blvd", "lat": 39.6213, "lng": -105.0800},
]
_WALMART_ABQ = [
    {"name": "Walmart Supercenter", "lat": 35.1977862, "lng": -106.6570174},
    {"name": "Walmart Supercenter", "lat": 35.0504145, "lng": -106.7075140},
    {"name": "Walmart Supercenter", "lat": 35.0982670, "lng": -106.5327120},
    {"name": "Walmart Supercenter", "lat": 35.1088980, "lng": -106.5074458},
    {"name": "Walmart Supercenter", "lat": 35.2057710, "lng": -106.6753890},
]
_WALMART_DALLAS = [
    {"name": "Walmart Supercenter - Garland",       "lat": 32.9126, "lng": -96.6388},
    {"name": "Walmart Supercenter - Mesquite",      "lat": 32.7874, "lng": -96.5998},
    {"name": "Walmart Supercenter - Irving",        "lat": 32.8573, "lng": -97.0094},
    {"name": "Walmart Supercenter - Duncanville",   "lat": 32.6468, "lng": -96.9114},
    {"name": "Walmart Supercenter - Grand Prairie", "lat": 32.7459, "lng": -97.0208},
]
_WALMART_TARRANT = [
    {"name": "Walmart Supercenter - N Fort Worth",  "lat": 32.8742, "lng": -97.3476},
    {"name": "Walmart Supercenter - S Fort Worth",  "lat": 32.6649, "lng": -97.3418},
    {"name": "Walmart Supercenter - W Fort Worth",  "lat": 32.7452, "lng": -97.4583},
    {"name": "Walmart Supercenter - Burleson",      "lat": 32.5416, "lng": -97.3207},
    {"name": "Walmart Supercenter - Haltom City",   "lat": 32.8092, "lng": -97.2686},
]
_WALMART_BEXAR = [
    {"name": "Walmart Supercenter - SW Military",   "lat": 29.3752, "lng": -98.5622},
    {"name": "Walmart Supercenter - Culebra",       "lat": 29.4955, "lng": -98.6244},
    {"name": "Walmart Supercenter - Thousand Oaks", "lat": 29.5601, "lng": -98.4073},
    {"name": "Walmart Supercenter - Potranco",      "lat": 29.4580, "lng": -98.7002},
    {"name": "Walmart Supercenter - SE Loop 410",   "lat": 29.3563, "lng": -98.4028},
]
_WALMART_HOUSTON = [
    {"name": "Walmart Supercenter - Hwy 6 N",       "lat": 29.8195, "lng": -95.6448},
    {"name": "Walmart Supercenter - FM 1960 W",     "lat": 29.9304, "lng": -95.5886},
    {"name": "Walmart Supercenter - Gessner",       "lat": 29.7611, "lng": -95.5316},
    {"name": "Walmart Supercenter - Katy Fwy",      "lat": 29.7654, "lng": -95.4951},
    {"name": "Walmart Supercenter - Fuqua",         "lat": 29.6226, "lng": -95.4215},
]
_WALMART_PHOENIX = [
    {"name": "Walmart Supercenter - W Indian School Rd", "lat": 33.4944, "lng": -112.1480},
    {"name": "Walmart Supercenter - N 35th Ave",         "lat": 33.5729, "lng": -112.1302},
    {"name": "Walmart Supercenter - E McDowell Rd",      "lat": 33.4718, "lng": -112.0188},
    {"name": "Walmart Supercenter - Scottsdale McDowell", "lat": 33.4690, "lng": -111.9090},
    {"name": "Walmart Supercenter - Mesa Superstition",   "lat": 33.4060, "lng": -111.7700},
    {"name": "Walmart Supercenter - Mesa Country Club",   "lat": 33.3920, "lng": -111.8300},
    {"name": "Walmart Supercenter - Tempe Baseline Rd",  "lat": 33.3810, "lng": -111.9440},
    {"name": "Walmart Supercenter - Chandler Alma School","lat": 33.3050, "lng": -111.8240},
    {"name": "Walmart Supercenter - Gilbert Williams Fld","lat": 33.3330, "lng": -111.7350},
    {"name": "Walmart Supercenter - Glendale Bethany",   "lat": 33.5480, "lng": -112.1950},
]

# ── Market definitions ────────────────────────────────────────────────────────
# Each city entry must have: map_center, overpass_bbox, walmart_static
# State-specific keys (ugrc_cities, fips, co_no, market) are fetcher-specific.
MARKETS = {
    "Utah": {
        "state_abbr": "UT",
        "fetcher": "utah",
        "cities": {
            "Salt Lake City": {
                "ugrc_cities": ["Salt Lake City", "South Salt Lake", "Murray", "Millcreek", "Holladay"],
                "map_center": [40.76, -111.89],
                "overpass_bbox": "40.4,-112.3,41.17,-111.7",
                "walmart_static": [],
            },
            "West Valley / Taylorsville": {
                "ugrc_cities": ["West Valley City", "Taylorsville"],
                "map_center": [40.69, -112.01],
                "overpass_bbox": "40.4,-112.3,41.17,-111.7",
                "walmart_static": [],
            },
            "Sandy / South Jordan": {
                "ugrc_cities": ["Sandy", "Midvale", "Riverton", "South Jordan"],
                "map_center": [40.58, -111.89],
                "overpass_bbox": "40.4,-112.3,41.17,-111.7",
                "walmart_static": [],
            },
            "Davis County": {
                "ugrc_cities": ["North Salt Lake", "Bountiful", "Centerville", "Farmington", "Kaysville", "Layton"],
                "map_center": [40.89, -111.87],
                "overpass_bbox": "40.5,-112.1,41.2,-111.7",
                "walmart_static": [],
            },
            "All SLC + Davis": {
                "ugrc_cities": None,
                "map_center": [40.76, -111.89],
                "overpass_bbox": "40.4,-112.3,41.17,-111.7",
                "walmart_static": [],
            },
        },
    },
    "New Mexico": {
        "state_abbr": "NM",
        "fetcher": "new_mexico",
        "cities": {
            "Albuquerque": {
                "situscity_filter": None,
                "map_center": [35.08, -106.65],
                "overpass_bbox": "34.9,-107.1,35.35,-106.4",
                "walmart_static": _WALMART_ABQ,
            },
        },
    },
    "Colorado": {
        "state_abbr": "CO",
        "fetcher": "colorado",
        "cities": {
            "Denver": {
                "fips": ["031"],
                "map_center": [39.7392, -104.9903],
                "overpass_bbox": "39.5,-105.3,40.0,-104.6",
                "walmart_static": _WALMART_DENVER,
            },
            "Aurora": {
                "fips": ["005"],
                "map_center": [39.7294, -104.8319],
                "overpass_bbox": "39.5,-105.3,40.0,-104.6",
                "walmart_static": _WALMART_DENVER,
            },
            "Lakewood / Arvada": {
                "fips": ["059"],
                "map_center": [39.7047, -105.0814],
                "overpass_bbox": "39.5,-105.3,40.0,-104.6",
                "walmart_static": _WALMART_DENVER,
            },
            "Westminster / Thornton": {
                "fips": ["001"],
                "map_center": [39.8836, -104.9819],
                "overpass_bbox": "39.7,-105.2,40.1,-104.8",
                "walmart_static": [],
            },
            "Colorado Springs": {
                "fips": ["041"],
                "map_center": [38.8339, -104.8214],
                "overpass_bbox": "38.6,-105.1,39.1,-104.6",
                "walmart_static": [],
            },
            "Boulder": {
                "fips": ["013"],
                "map_center": [40.0150, -105.2705],
                "overpass_bbox": "39.9,-105.5,40.2,-105.0",
                "walmart_static": [],
            },
            "Fort Collins": {
                "fips": ["069"],
                "map_center": [40.5853, -105.0844],
                "overpass_bbox": "40.4,-105.3,40.8,-104.9",
                "walmart_static": [],
            },
        },
    },
    "Arizona": {
        "state_abbr": "AZ",
        "fetcher": "arizona",
        "cities": {
            "Phoenix": {
                "city_filter": "PHOENIX",
                "map_center": [33.45, -112.07],
                "overpass_bbox": "33.25,-112.35,33.70,-111.60",
                "walmart_static": _WALMART_PHOENIX,
            },
            "Scottsdale": {
                "city_filter": "SCOTTSDALE",
                "map_center": [33.49, -111.89],
                "overpass_bbox": "33.25,-112.35,33.70,-111.60",
                "walmart_static": _WALMART_PHOENIX,
            },
            "Mesa": {
                "city_filter": "MESA",
                "map_center": [33.42, -111.83],
                "overpass_bbox": "33.25,-112.35,33.70,-111.60",
                "walmart_static": _WALMART_PHOENIX,
            },
            "Tempe": {
                "city_filter": "TEMPE",
                "map_center": [33.43, -111.94],
                "overpass_bbox": "33.25,-112.35,33.70,-111.60",
                "walmart_static": _WALMART_PHOENIX,
            },
            "Chandler": {
                "city_filter": "CHANDLER",
                "map_center": [33.31, -111.84],
                "overpass_bbox": "33.25,-112.35,33.70,-111.60",
                "walmart_static": _WALMART_PHOENIX,
            },
            "Gilbert": {
                "city_filter": "GILBERT",
                "map_center": [33.35, -111.79],
                "overpass_bbox": "33.25,-112.35,33.70,-111.60",
                "walmart_static": _WALMART_PHOENIX,
            },
            "Glendale": {
                "city_filter": "GLENDALE",
                "map_center": [33.53, -112.19],
                "overpass_bbox": "33.25,-112.35,33.70,-111.60",
                "walmart_static": _WALMART_PHOENIX,
            },
        },
    },
    "Florida": {
        "state_abbr": "FL",
        "fetcher": "florida",
        "cities": {
            "Tampa": {
                "co_no": 39,
                "map_center": [27.95, -82.46],
                "overpass_bbox": "27.78,-82.65,28.16,-82.17",
                "walmart_static": [
                    {"name": "Walmart Supercenter - N Dale Mabry",   "lat": 28.0634, "lng": -82.5077},
                    {"name": "Walmart Supercenter - E Hillsborough",  "lat": 27.9820, "lng": -82.3963},
                    {"name": "Walmart Supercenter - W Hillsborough",  "lat": 27.9820, "lng": -82.5000},
                    {"name": "Walmart Supercenter - Bearss Ave",      "lat": 28.0853, "lng": -82.4290},
                    {"name": "Walmart Supercenter - Palm River",      "lat": 27.9289, "lng": -82.3877},
                ],
            },
            "Jacksonville": {
                "co_no": 26,
                "map_center": [30.33, -81.66],
                "overpass_bbox": "30.10,-81.99,30.60,-81.38",
                "walmart_static": [
                    {"name": "Walmart Supercenter - Blanding Blvd",  "lat": 30.1641, "lng": -81.7310},
                    {"name": "Walmart Supercenter - Normandy Blvd",  "lat": 30.3048, "lng": -81.7629},
                    {"name": "Walmart Supercenter - Regency Square", "lat": 30.3128, "lng": -81.5576},
                    {"name": "Walmart Supercenter - Dunn Ave",       "lat": 30.3932, "lng": -81.6580},
                    {"name": "Walmart Supercenter - Beach Blvd",     "lat": 30.2690, "lng": -81.5668},
                ],
            },
            "Orlando": {
                "co_no": 58,
                "map_center": [28.54, -81.38],
                "overpass_bbox": "28.32,-81.57,28.78,-80.97",
                "walmart_static": [
                    {"name": "Walmart Supercenter - W Colonial Dr",    "lat": 28.5469, "lng": -81.4518},
                    {"name": "Walmart Supercenter - E Colonial Dr",    "lat": 28.5455, "lng": -81.2174},
                    {"name": "Walmart Supercenter - Landstreet Rd",    "lat": 28.4754, "lng": -81.4195},
                    {"name": "Walmart Supercenter - Irlo Bronson Hwy", "lat": 28.3185, "lng": -81.5143},
                    {"name": "Walmart Supercenter - Rolling Oaks",     "lat": 28.4020, "lng": -81.2899},
                ],
            },
        },
    },
}
