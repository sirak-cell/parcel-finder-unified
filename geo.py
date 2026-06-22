import math, pandas as pd

def haversine_miles(lat1, lng1, lat2, lng2):
    R = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return R * 2 * math.asin(math.sqrt(a))

def filter_by_walmart_proximity(df, walmarts, min_mi=0.0, max_mi=5.0):
    if df.empty or not walmarts: return df
    names, dists = [], []
    for _, row in df.iterrows():
        bd, bn = float("inf"), ""
        for w in walmarts:
            d = haversine_miles(row["lat"], row["lng"], w["lat"], w["lng"])
            if d < bd: bd, bn = d, w["name"]
        names.append(bn); dists.append(round(bd, 2))
    df = df.copy()
    df["nearest_walmart_name"] = names
    df["nearest_walmart_mi"]   = dists
    return df[(df["nearest_walmart_mi"] >= min_mi) & (df["nearest_walmart_mi"] <= max_mi)].reset_index(drop=True)
