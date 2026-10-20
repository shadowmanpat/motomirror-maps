#!/usr/bin/env python3
"""Builds the country maps for the MotoMirror app.

Each country is one PMTiles file up to z14, clipped to the Geofabrik border,
cut from the daily Protomaps planet build (OpenStreetMap data, ODbL). Nothing
downloads the planet: `pmtiles extract` reads only the country's byte ranges.

The whole world runs as one job per continent (see maps.yml), because one job
would not finish within GitHub's six-hour limit. Each job writes a catalogue
fragment; the last job merges them into countries.json.

    ./build.py build --continent europe --release TAG   # one continent
    ./build.py build GR CY --out out                    # chosen countries
    ./build.py build --dry-run --continent antarctica   # sizes only
    ./build.py publish --fragments out --release TAG    # merge and publish

Why clip to the border instead of a bounding box: the box around Greece also
takes in Izmir, Tirana and Sofia. Measured (19 Oct): 519 MB instead of 272 MB.

Needs `pmtiles` (brew install pmtiles) and, for uploads, `gh`.
"""
import argparse
import collections
import datetime
import glob
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request

INDEX_URL = "https://download.geofabrik.de/index-v1-nogeom.json"

# The continents the app lists, in the order it shows them. Geofabrik files
# Russia under its own top-level region; it is counted as Europe here.
CONTINENTS = ["europe", "asia", "africa", "north-america", "central-america",
              "south-america", "australia-oceania", "antarctica"]
CONTINENT_ALIAS = {"russia": "europe"}

# GitHub rejects release files over 2 GiB. Germany and France are 2.5 GB each
# (measured 19 Oct), so they are split into parts that the app joins again.
# 1.9 GiB leaves some margin.
PART_BYTES = 1900 * 1024 * 1024

# Up to z14: the vector map scales up from there without blurring. z15 almost
# doubles the file (measured: 519 → 983 MB for the box around Greece).
MAX_ZOOM = 14

# Some servers refuse Python's default user agent, and that failure only shows
# on GitHub's runners, never on a Mac. Every request names itself.
USER_AGENT = "motomirror-maps-build/1.0 (+https://github.com/shadowmanpat/motomirror-maps)"


def fetch(url, method="GET", timeout=60):
    request = urllib.request.Request(url, method=method, headers={"User-Agent": USER_AGENT})
    return urllib.request.urlopen(request, timeout=timeout)


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
            fetch(url, method="HEAD", timeout=15)
            return url
        except Exception as error:
            print(f"  {day}: {error}", file=sys.stderr)
    sys.exit("No daily build found on build.protomaps.com")


def countries():
    """Every country with a region of its own, from the Geofabrik index.

    Returns code → {"poly": border URL, "continent": name}. Regions shared by
    several countries (the Gulf states, Israel and Palestine…) are left out:
    cutting one would give every country the whole shared border."""
    with fetch(INDEX_URL) as response:
        features = json.load(response)["features"]
    by_id = {feature["properties"]["id"]: feature["properties"] for feature in features}

    def continent(region):
        while region.get("parent") in by_id:
            region = by_id[region["parent"]]
        return CONTINENT_ALIAS.get(region["id"], region["id"])

    grouped = collections.defaultdict(list)
    for feature in features:
        properties = feature["properties"]
        codes = properties.get("iso3166-1:alpha2") or []
        if len(codes) == 1:
            grouped[codes[0]].append(properties)

    found = {}
    for code, regions in grouped.items():
        ids = {region["id"] for region in regions}
        # The country itself, not one of its provinces.
        top = [region for region in regions if region.get("parent") not in ids] or regions
        found[code] = {"poly": top[0]["urls"]["pbf"].replace("-latest.osm.pbf", ".poly"),
                       "continent": continent(top[0])}
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


def bounds(region):
    """[west, south, east, north] of the border, so the app can tell which
    countries are around the rider before any of them is downloaded."""
    points = [point for polygon in region["coordinates"] for point in polygon[0]]
    return [round(min(p[0] for p in points), 3), round(min(p[1] for p in points), 3),
            round(max(p[0] for p in points), 3), round(max(p[1] for p in points), 3)]


def extract(source, code, region, out, dry_run):
    """Cuts one country. Returns its size in bytes (an estimate with `dry_run`)."""
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
    """Uploads, then deletes: each runner has ~14 GB free, and a continent can
    be tens of GB in total."""
    paths = [os.path.join(out, name) for name in files]
    subprocess.run(["gh", "release", "upload", tag, "--clobber"] + paths, check=True)
    for path in paths:
        os.unlink(path)


def build(arguments):
    index = countries()
    if arguments.continent:
        if arguments.continent not in CONTINENTS:
            sys.exit(f"Unknown continent: {arguments.continent}")
        codes = sorted(code for code, info in index.items()
                       if info["continent"] == arguments.continent)
        label = arguments.continent
    else:
        codes = [code.upper() for code in arguments.codes]
        label = "custom"
    unknown = [code for code in codes if code not in index]
    if unknown:
        sys.exit(f"Not in Geofabrik: {' '.join(unknown)}")

    os.makedirs(arguments.out, exist_ok=True)
    source = latest_build()
    built_on = re.search(r"(\d{8})\.pmtiles$", source)
    date = (datetime.datetime.strptime(built_on.group(1), "%Y%m%d").date().isoformat()
            if built_on else datetime.date.today().isoformat())
    print(f"▸ {label}: {len(codes)} countries from {source}", flush=True)

    catalogue = {"version": 1, "tag": arguments.release or date, "date": date,
                 "countries": {}}
    failed = []
    for code in codes:
        region = poly_to_geojson(fetch(index[code]["poly"]).read().decode())
        size = extract(source, code, region, arguments.out, arguments.dry_run)
        if size is None:
            print(f"✗ {code}: failed", file=sys.stderr, flush=True)
            failed.append(code)
            continue
        print(f"✓ {code} {size / 1e6:.0f} MB", flush=True)
        entry = {"bytes": size, "continent": index[code]["continent"],
                 "bounds": bounds(region)}
        if arguments.dry_run:
            catalogue["countries"][code] = dict(entry, parts=[])
            continue
        parts = split(os.path.join(arguments.out, f"{code}.pmtiles"))
        catalogue["countries"][code] = dict(entry, parts=parts)
        if arguments.release:
            upload(arguments.release, parts, arguments.out)

    fragment = os.path.join(arguments.out, f"countries-{label}.json")
    with open(fragment, "w") as handle:
        json.dump(catalogue, handle, indent=1, sort_keys=True)
    total = sum(country["bytes"] for country in catalogue["countries"].values())
    print(f"▸ {label}: {len(catalogue['countries'])} countries, {total / 1e9:.1f} GB")
    if failed:
        sys.exit(f"Failed: {' '.join(failed)}")


def publish(arguments):
    """Merges the fragments of every continent and makes the release the latest.

    Only runs after every continent job succeeded (see maps.yml), so "latest"
    never shows a partial catalogue."""
    merged = {"version": 1, "tag": arguments.release, "date": None, "countries": {}}
    for path in sorted(glob.glob(os.path.join(arguments.fragments, "countries-*.json"))):
        with open(path) as handle:
            fragment = json.load(handle)
        merged["date"] = merged["date"] or fragment["date"]
        merged["countries"].update(fragment["countries"])
    if not merged["countries"]:
        sys.exit("No countries in any fragment")
    out = os.path.join(arguments.fragments, "countries.json")
    with open(out, "w") as handle:
        json.dump(merged, handle, indent=1, sort_keys=True)
    print(f"▸ {len(merged['countries'])} countries in the catalogue")
    subprocess.run(["gh", "release", "upload", arguments.release, "--clobber", out], check=True)
    subprocess.run(["gh", "release", "edit", arguments.release, "--draft=false", "--latest"],
                   check=True)


def main():
    global PART_BYTES
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)

    cut = commands.add_parser("build", help="cut countries and write a catalogue fragment")
    cut.add_argument("codes", nargs="*", help="country codes (or use --continent)")
    cut.add_argument("--continent", help="every country of one continent")
    cut.add_argument("--out", default="out")
    cut.add_argument("--dry-run", action="store_true")
    cut.add_argument("--release", help="release tag to upload parts to")
    cut.add_argument("--part-mb", type=int,
                     help="part size in MB (to test joining parts in the app)")

    join = commands.add_parser("publish", help="merge fragments and publish as latest")
    join.add_argument("--fragments", default="out")
    join.add_argument("--release", required=True)

    arguments = parser.parse_args()
    if arguments.command == "build":
        if arguments.part_mb:
            PART_BYTES = arguments.part_mb * 1024 * 1024
        build(arguments)
    else:
        publish(arguments)


if __name__ == "__main__":
    main()
