#!/usr/bin/env python3
"""
Build the offline outline-map data in www/lib/geo/ from Natural Earth.

Downloads Natural Earth (public domain) country and state/province outlines,
strips them down to the few properties the dashboard needs, rounds the
coordinates, and writes compact GeoJSON files:

  www/lib/geo/countries.geojson  - world countries (1:50m, Great Lakes cut out)
  www/lib/geo/states.geojson     - US states + DC and Canadian provinces/territories

The output files are committed to the repo, so you only need to run this if you
want to regenerate them (e.g. different detail level). Requires internet.
"""

import json
import os
import urllib.request

NE_BASE = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson"
COUNTRIES_SRC = f"{NE_BASE}/ne_50m_admin_0_countries_lakes.geojson"
STATES_SRC = f"{NE_BASE}/ne_50m_admin_1_states_provinces_lakes.geojson"

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "www", "lib", "geo")

# Decimal places kept on coordinates. 2 places is about 1 km, plenty for a
# continent/world-scale dashboard map.
PRECISION = 2

# Natural Earth name fields used to match N3FJP country names.
COUNTRY_NAME_FIELDS = [
    "ADMIN", "NAME", "NAME_LONG", "FORMAL_EN", "NAME_SORT", "NAME_ALT",
    "BRK_NAME", "GEOUNIT", "SUBUNIT", "NAME_CIAWF", "ISO_A3", "ISO_A2",
]


def fetch_json(url: str) -> dict:
    print(f"Downloading {url}")
    with urllib.request.urlopen(url, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def round_ring(ring):
    out = []
    for x, y, *_ in ring:
        pt = [round(x, PRECISION), round(y, PRECISION)]
        if not out or out[-1] != pt:
            out.append(pt)
    if out and out[0] != out[-1]:
        out.append(out[0])
    # A valid ring needs at least 4 points (3 distinct + closing point)
    return out if len(out) >= 4 else None


def round_polygon(poly):
    rings = [r for r in (round_ring(r) for r in poly) if r]
    # Drop the polygon if its outer ring collapsed
    if not rings or round_ring(poly[0]) is None:
        return None
    return rings


def slim_geometry(geom):
    if geom is None:
        return None
    if geom["type"] == "Polygon":
        poly = round_polygon(geom["coordinates"])
        return {"type": "Polygon", "coordinates": poly} if poly else None
    if geom["type"] == "MultiPolygon":
        polys = [p for p in (round_polygon(p) for p in geom["coordinates"]) if p]
        if not polys:
            return None
        if len(polys) == 1:
            return {"type": "Polygon", "coordinates": polys[0]}
        return {"type": "MultiPolygon", "coordinates": polys}
    return None


def write_geojson(path: str, features: list) -> None:
    fc = {"type": "FeatureCollection", "features": features}
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(fc, f, ensure_ascii=False, separators=(",", ":"))
    print(f"Wrote {path} ({len(features)} features, {os.path.getsize(path) // 1024} KB)")


def build_countries() -> list:
    src = fetch_json(COUNTRIES_SRC)
    features = []
    for f in src["features"]:
        p = f["properties"]
        geom = slim_geometry(f["geometry"])
        if not geom:
            continue
        names = []
        for field in COUNTRY_NAME_FIELDS:
            v = (p.get(field) or "").strip()
            if v and v != "-99" and v not in names:
                names.append(v)
        features.append({
            "type": "Feature",
            "properties": {"name": p["ADMIN"], "iso": p.get("ADM0_A3"), "names": names},
            "geometry": geom,
        })
    return features


def build_states() -> list:
    src = fetch_json(STATES_SRC)
    features = []
    for f in src["features"]:
        p = f["properties"]
        if p.get("adm0_a3") not in ("USA", "CAN"):
            continue
        geom = slim_geometry(f["geometry"])
        if not geom:
            continue
        features.append({
            "type": "Feature",
            "properties": {"code": p["postal"], "name": p["name"], "country": p["adm0_a3"]},
            "geometry": geom,
        })
    return features


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    write_geojson(os.path.join(OUT_DIR, "countries.geojson"), build_countries())
    write_geojson(os.path.join(OUT_DIR, "states.geojson"), build_states())
    print("\nDone. Map outlines are in www/lib/geo/ (Natural Earth, public domain).")


if __name__ == "__main__":
    main()
