# MotoMirror — offline country maps

Offline country maps for the MotoMirror app, so the motorcycle dashboard can show a map
while the phone is locked and without signal.

## What each release contains

| File | What it is |
|---|---|
| `countries.json` | The catalogue: release tag, data date, and for each country its size and parts |
| `GR.pmtiles`, `CY.pmtiles`, … | One country (ISO code), [PMTiles v3](https://github.com/protomaps/PMTiles) vector tiles up to z14, clipped to its border |
| `DE.pmtiles.1`, `DE.pmtiles.2` | A country over 2 GiB (GitHub's limit), split into parts that are joined in order |

The app reads the catalogue from the latest release:

    https://github.com/shadowmanpat/motomirror-maps/releases/latest/download/countries.json

## How they are built

The [`Maps`](.github/workflows/maps.yml) workflow runs [`build.py`](build.py) once a month
(or on "Run workflow"): it cuts each country out of the daily [Protomaps](https://protomaps.com)
build along its [Geofabrik](https://download.geofabrik.de) border and uploads everything to
a new release. A release becomes "latest" only once **every** country is uploaded.

Locally (needs `brew install pmtiles`):

    ./build.py GR CY --out out          # two countries and their catalogue
    ./build.py --dry-run                # sizes only, every country

## Source and license

Map data © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors,
available under the [Open Database License (ODbL) 1.0](https://opendatacommons.org/licenses/odbl/1-0/).
Built from the daily [Protomaps](https://protomaps.com) basemap; low-zoom data from
[Natural Earth](https://www.naturalearthdata.com) (public domain).
