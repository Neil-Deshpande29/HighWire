# CoStar, Vexcel and HighWire

The CoStar track asks for at least two of its three data layers (CoStar data extract, Matterport, Vexcel).
HighWire uses **Vexcel** (live) and **CoStar** (one import away). This page says what each does and how to load it.

## Vexcel: live

Set `VEXCEL_API_KEY` in `.env` (git-ignored). The key is sent only in the `X-API-KEY` header and never saved anywhere else.

| What | Where it shows | Command | Code |
|---|---|---|---|
| Aerial imagery under the map | Layers, "Aerial imagery (Vexcel)" | `make imagery` | `backend/la/basemap.py`, `backend/la/vexcel.py` |
| Trees near each risky line's wires | "Risk by line", the line's sheet, the plan's reasons | `make trees` | `backend/la/trees.py` |

- Imagery comes from `ortho/extract`, 12 areas stitched into one picture. The hackathon key stops near 34.32 N;
  north of that the public USGS picture shows through, and `GET /la/imagery` says how much Vexcel covers.
- Trees come from `elements/detect` with the label "tree" on a 300 m square at each of a line's most fire-prone spots.
  Trees within 100 m of the line are counted once. A line with 20 or more is named first when the planner orders cuts.
- Tree counts and the vegetation share (from USDA infrared photos) never change the fire index. They order the work.
- Request shapes follow Vexcel's hackathon quickstart. Imagery and counts are cached under `data/processed/`, git-ignored.

## CoStar: load the extract

The extract (Houston and Los Angeles) is handed out in the track Discord. One teammate does this once:

1. In the track Discord, run `/costar-keys`. The reply has the download link and its password.
2. Download and unzip it anywhere on this machine.
3. Run `make costar FILE=/path/to/the/extract` (a file or a folder; csv, xlsx, json or geojson).
4. Restart the backend.

The importer finds columns by name (latitude, longitude, property type, ...), keeps properties inside the LA grid,
prints what it used, and writes `data/processed/costar/buildings.json` (git-ignored). It stops with a clear message if
it finds no coordinates. Until then the building list is 218 hospitals and water plants from OpenStreetMap, and the
screen says so.

## What the building data drives

**The Buildings agent** (`buildings_agent`) compares what each risky line feeds and reports it on a priority ladder:

| Tier | Who | Building kinds |
|---|---|---|
| 1 | Life safety | hospitals, emergency services |
| 2 | Lifelines | water plants |
| 3 | Cannot ride through an outage | medical sites, data centers, cold stores, schools |
| 4 | Homes and industry | residential, hotels, industrial |
| 5 | Offices and shops | offices, retail, other |

- Each CoStar property type maps to a kind (`buildings.kind_of`) and each property to its nearest substation.
- When two lines are equally risky, the one that reaches a lower tier is cut first.
- Among safe plans, the one that puts fewer high-tier buildings on backup ranks first.
- Every plan and every plan step lists what it puts on backup power, by tier.
- The AI team gets the same data through the `get_building_impact` tool; the rule-based planner reads it directly.

The ladder and its weights are in `backend/la/buildings.py` (`TIERS`, `WEIGHTS`). They are a stated judgement, not a model.

## Honest limits

- Lines of 66 kV and up are modelled. "Loses power" means the substation serving the area is cut off; no feeder is traced to a building.
- Matterport is not used.
