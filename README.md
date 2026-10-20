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

The [`Maps`](.github/workflows/maps.yml) workflow builds the whole world, one job per
continent, in parallel (GitHub's six-hour limit is per job). Each job cuts its
countries from the daily [Protomaps](https://protomaps.com) build along their
[Geofabrik](https://download.geofabrik.de) borders and uploads them to one draft
release. A final job merges the catalogues and makes the release "latest", so the app
never sees half a world.

Run it by hand: Actions → **Maps** → Run workflow. Leave the country field empty for the
whole world, or give codes (`GR IT`) to build just those as a test.

Locally (needs `brew install pmtiles`):

    ./build.py build GR CY --out out                 # two countries and their fragment
    ./build.py build --continent europe --dry-run    # sizes only, one continent

## Source and license

Map data © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors,
available under the [Open Database License (ODbL) 1.0](https://opendatacommons.org/licenses/odbl/1-0/).
Built from the daily [Protomaps](https://protomaps.com) basemap; low-zoom data from
[Natural Earth](https://www.naturalearthdata.com) (public domain).
