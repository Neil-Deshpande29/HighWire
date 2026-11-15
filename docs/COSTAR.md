# CoStar, Vexcel and HighWire

The CoStar track asks for at least two of its three data layers (CoStar data extract, Matterport, Vexcel).
HighWire uses **Vexcel** (live) and the **CoStar data kit** (imported). This page says what each does and how to load it.

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

## CoStar: loaded from the data kit

The kit (Houston and Los Angeles) comes from the track Discord (`/costar-keys`). It is licensed, so it stays outside
the repo. Unzip it anywhere, then:

```
make costar FILE=/path/to/costar-hackathon-kit
```

and restart the backend. The importer (`backend/la/costar_import.py`) reads the kit as its own notes describe:

| From the kit | Used for |
|---|---|
| `data/commercial-property.jsonl`: type, subtype, location | which rung of the ladder a building is on, and which substation serves it |
| `propertyApartment.totalNumUnits` | apartment homes behind a cut |
| `propertyApartment.apartmentLifestyleType` = Senior | senior housing, tier 3 |
| `data/location-occupancy.jsonl` + `data/company-detail.jsonl`: current tenants by industry | an office where half or more of the known tenants are in health care counts as a medical site |
| `schemas/enums.json` | the names behind every code |

It writes `data/processed/costar/buildings.json` (git-ignored). On the hackathon kit that is 1,578 Los Angeles
properties with 89,986 apartment homes: 1 hospital, 12 medical sites, 10 senior housing sites, 1 school, 1,407
apartment buildings, 34 industrial sites, 1 hotel, 86 offices, 24 shops. Land parcels are left out. Any other table
(csv, xlsx, json, geojson) also works: columns are found by name.

**Where it shows.** The kit covers the properties on CoStar's Matterport list, which sit in the central basin
(Hollywood, downtown, the Westside). 61 substations serve them and 33 single lines would cut some of them off. Try
"Find a line" with `L182`: turning it off cuts 2 hospitals, 2 medical sites, 106 apartment buildings (3,668 homes) and
12 offices. The fire-risk lines in the Woolsey replay are in the western valleys, outside that area, so that plan's
own cut reaches no CoStar property; its hospitals and water plants come from OpenStreetMap.

## What the building data drives

**The Buildings agent** (`buildings_agent`) compares what each risky line feeds and reports it on a priority ladder:

| Tier | Who | Building kinds |
|---|---|---|
| 1 | Life safety | hospitals, emergency services |
| 2 | Lifelines | water plants |
| 3 | Cannot ride through an outage | medical sites, data centers, senior housing, cold stores, schools |
| 4 | Homes and industry | residential, hotels, industrial |
| 5 | Offices and shops | offices, retail, other |

- Each CoStar property type maps to a kind (`buildings.kind_of`) and each property to its nearest substation.
- When two lines are equally risky, the one that reaches a lower tier is cut first.
- Among safe plans, the one that puts fewer high-tier buildings on backup ranks first.
- Every plan and every plan step lists what it cuts off, by tier, with the apartment homes behind it.
- Clicking any line says what switching it off would cut (`POST /la/cut`), not only the lines at high risk.
- The AI team gets the same data through the `get_building_impact` tool; the rule-based planner reads it directly.

The ladder and its weights are in `backend/la/buildings.py` (`TIERS`, `WEIGHTS`). They are a stated judgement, not a model.

## Honest limits

- Lines of 66 kV and up are modelled. "Loses power" means the substation serving the area is cut off; no feeder is traced to a building.
- Matterport is not used.
