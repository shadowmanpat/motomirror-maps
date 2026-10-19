#!/usr/bin/env python3
"""Builds the country maps for the MotoMirror app.

Each country is one PMTiles file up to z14, clipped to the Geofabrik border,
cut from the daily Protomaps planet build (OpenStreetMap data, ODbL). Nothing
downloads the planet: `pmtiles extract` reads only the country's byte ranges.

    ./build.py GR CY                     → out/GR.pmtiles, out/CY.pmtiles, out/countries.json
    ./build.py --dry-run                 → sizes only, for every country
    ./build.py --release 2026-10-18      → and upload to a release (needs `gh`)

Why clip to the border instead of a bounding box: the box around Greece also
takes in Izmir, Tirana and Sofia. Measured (19 Oct): 519 MB instead of 272 MB.

Needs `pmtiles` (brew install pmtiles) and, for uploads, `gh`.
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request

# The app's countries (`MapRegion.all`). The app lists whatever `countries.json`
# says, so a country missing here simply doesn't show up.
COUNTRIES = ("GR AL MK BG RS ME BA HR SI RO TR IT AT CH DE FR ES PT CZ SK HU PL NL "
             "BE GB IE DK SE NO FI EE LV LT UA MD CY MT").split()

# GitHub rejects release files over 2 GiB. Germany and France are 2.5 GB each
# (measured 19 Oct), so they are split into parts that the app joins again.
# 1.9 GiB leaves some margin.
PART_BYTES = 1900 * 1024 * 1024

# Up to z14: the vector map scales up from there without blurring. z15 almost
# doubles the file (measured: 519 → 983 MB for the box around Greece).
MAX_ZOOM = 14


def latest_build():
    """The newest daily build. Old ones are deleted after a few days, so it is
    looked up on every run instead of being written down here."""
    if os.environ.get("PROTOMAPS_BUILD"):
        return os.environ["PROTOMAPS_BUILD"]
    today = datetime.date.today()
    for back in range(8):
        day = (today - datetime.timedelta(days=back)).strftime("%Y%m%d")
        url = f"https://build.protomaps.com/{day}.pmtiles"
        try:
            urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=15)
            return url
        except Exception:
            continue
    sys.exit("No daily build found on build.protomaps.com")


def boundaries():
    """Country code → border (.poly) from the Geofabrik index. Picks the region
    that is **only** that country (not "Ireland and Northern Ireland" for GB),
    and the country itself, not one of its provinces."""
    with urllib.request.urlopen("https://download.geofabrik.de/index-v1-nogeom.json",
                                timeout=60) as response:
        features = json.load(response)["features"]
    found = {}
    for code in COUNTRIES:
        matches = [f["properties"] for f in features
                   if f["properties"].get("iso3166-1:alpha2") == [code]]
        ids = {m["id"] for m in matches}
        top = [m for m in matches if m.get("parent") not in ids] or matches
        if top:
            found[code] = top[0]["urls"]["pbf"].replace("-latest.osm.pbf", ".poly")
    return found


def poly_to_geojson(text):
    """The .poly format: sections of coordinates, where sections starting with
    "!" are holes. `pmtiles extract --region` wants GeoJSON."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    polygons, ring, hole = [], None, False
    for line in lines[1:]:
        if line == "END":
            if ring is None:
                break
            if ring[0] != ring[-1]:
                ring.append(ring[0])
            if hole and polygons:
                polygons[-1].append(ring)
            else:
                polygons.append([ring])
            ring = None
        elif ring is None:
            hole = line.startswith("!")
            ring = []
        else:
            lon, lat = map(float, line.split())
            ring.append([lon, lat])
    return {"type": "MultiPolygon", "coordinates": polygons}


def extract(source, code, poly_url, out, dry_run):
    """Cuts one country. Returns its size in bytes (an estimate with `dry_run`)."""
    with urllib.request.urlopen(poly_url, timeout=60) as response:
        region = poly_to_geojson(response.read().decode())
    with tempfile.NamedTemporaryFile("w", suffix=".geojson", delete=False) as handle:
        json.dump(region, handle)
        region_path = handle.name
    target = os.path.join(out, f"{code}.pmtiles")
    command = ["pmtiles", "extract", source, target, f"--region={region_path}",
               f"--maxzoom={MAX_ZOOM}"] + (["--dry-run"] if dry_run else [])
    # Protomaps now and then answers HTTP 500 (it happened for Malta): three
    # attempts before giving up.
    for attempt in range(3):
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode == 0:
            break
        print(f"  {code}: attempt {attempt + 1} failed", file=sys.stderr)
    os.unlink(region_path)
    if result.returncode != 0:
        print(result.stderr[-500:], file=sys.stderr)
        return None
    if dry_run:
        match = re.search(r"archive size of ([\d.]+) (\w+)", result.stderr + result.stdout)
        if not match:
            return None
        scale = {"B": 1, "kB": 1e3, "MB": 1e6, "GB": 1e9}.get(match.group(2), 1)
        return int(float(match.group(1)) * scale)
    subprocess.run(["pmtiles", "verify", target], check=True, capture_output=True)
    return os.path.getsize(target)


def split(path):
    """Parts under GitHub's limit, named `XX.pmtiles.1`, `.2`… The app joins them
    in the order the catalogue lists them."""
    size = os.path.getsize(path)
    if size <= PART_BYTES:
        return [os.path.basename(path)]
    parts = []
    with open(path, "rb") as source:
        index = 1
        while True:
            chunk = source.read(PART_BYTES)
            if not chunk:
                break
            name = f"{os.path.basename(path)}.{index}"
            with open(os.path.join(os.path.dirname(path), name), "wb") as part:
                part.write(chunk)
            parts.append(name)
            index += 1
    os.unlink(path)
    return parts


def upload(tag, files, out):
    """Uploads, then deletes: the GitHub runner has ~14 GB free, and all the
    countries together are ~22 GB."""
    paths = [os.path.join(out, name) for name in files]
    subprocess.run(["gh", "release", "upload", tag, "--clobber"] + paths, check=True)
    for path in paths:
        os.unlink(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("codes", nargs="*", help="country codes (empty = all)")
    parser.add_argument("--out", default="out")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--release", help="release tag to upload to")
    parser.add_argument("--part-mb", type=int,
                        help="part size in MB (to test joining parts in the app)")
    arguments = parser.parse_args()

    codes = [code.upper() for code in arguments.codes] or COUNTRIES
    if arguments.part_mb:
        global PART_BYTES
        PART_BYTES = arguments.part_mb * 1024 * 1024
    os.makedirs(arguments.out, exist_ok=True)
    source = latest_build()
    built_on = re.search(r"(\d{8})\.pmtiles$", source)
    date = (datetime.datetime.strptime(built_on.group(1), "%Y%m%d").date().isoformat()
            if built_on else datetime.date.today().isoformat())
    print(f"▸ source {source}")
    polys = boundaries()

    tag = arguments.release
    if tag:
        # A draft until everything is uploaded: "latest" only moves when the new
        # catalogue is complete, never halfway through.
        subprocess.run(["gh", "release", "create", tag, "--draft", "--title", f"Maps {date}",
                        "--notes", "Map data © OpenStreetMap contributors, ODbL. "
                                   "Built from the Protomaps daily basemap."],
                       capture_output=True)

    catalogue = {"version": 1, "tag": tag or date, "date": date, "countries": {}}
    failed = []
    for code in codes:
        if code not in polys:
            print(f"✗ {code}: not in Geofabrik", file=sys.stderr)
            failed.append(code)
            continue
        size = extract(source, code, polys[code], arguments.out, arguments.dry_run)
        if size is None:
            print(f"✗ {code}: failed", file=sys.stderr)
            failed.append(code)
            continue
        print(f"✓ {code} {size / 1e6:.0f} MB", flush=True)
        if arguments.dry_run:
            catalogue["countries"][code] = {"bytes": size, "parts": []}
            continue
        parts = split(os.path.join(arguments.out, f"{code}.pmtiles"))
        catalogue["countries"][code] = {"bytes": size, "parts": parts}
        if tag:
            upload(tag, parts, arguments.out)

    path = os.path.join(arguments.out, "countries.json")
    with open(path, "w") as handle:
        json.dump(catalogue, handle, indent=1, sort_keys=True)
    total = sum(country["bytes"] for country in catalogue["countries"].values())
    print(f"▸ {len(catalogue['countries'])} countries, {total / 1e9:.1f} GB")
    if tag and not arguments.dry_run:
        subprocess.run(["gh", "release", "upload", tag, "--clobber", path], check=True)
        # "latest" only with **all** countries: the app lists what the catalogue
        # says, and a release with just Cyprus would hide all the others.
        if not failed and set(codes) == set(COUNTRIES):
            subprocess.run(["gh", "release", "edit", tag, "--draft=false", "--latest"], check=True)
        else:
            print("▸ partial build: the release stays a draft")
    if failed:
        sys.exit(f"Failed: {' '.join(failed)}")


if __name__ == "__main__":
    main()
