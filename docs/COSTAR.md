# CoStar and Vexcel in HighWire

What is built, what data it runs on today, and the exact steps to switch to CoStar and Vexcel data.

## What the operator sees

1. **Aerial imagery under the map.** Layers menu, "Aerial imagery". The 3D terrain shows a stitched aerial picture of
   the region; lines, labels and risk colours are drawn on top. The picture is muted so they stay legible.
2. **Vegetation near the wires.** Each line over the fire alarm shows how much of the ground within 100 m of its
   conductors is vegetated, measured from aerial photos of its most fire-prone stretches: "Vegetation near the wires:
   high (71% within 100 m)". A high reading marks the line as one to act on first.
3. **What a cut would hit.** Each of the eight riskiest lines shows what loses grid power if that one line is switched
   off: "If cut: 2 hospitals, 5 water plants on backup · 55,680 customers".
4. **Buildings in every plan.** Each plan card says what it leaves without grid power, by kind of building, and names
   the sites under Details. Among safe plans, the one that cuts off less critical buildings ranks first.
5. **Warnings on each plan step.** Under Details, a chip on each step opens the five buildings that step cuts off,
   heaviest first, with kind, backup hours and data source. The level (high, medium, low) is set in code and shown as
   a shape, a word and a colour.
6. **Photos of the selected line.** Selecting a line, or finding it by id in "Find a line", opens its aerial photos
   with the vegetation reading and the facts at each photo.

A link reopens any view: `/?event=E1&hour=11&imagery=1` is the Woolsey replay at 11 AM with aerial imagery on.

Neither factor changes the calibrated fire index. They rank lines and plans; the physics check still decides what is safe.

## What it runs on today

No CoStar, Vexcel or Matterport call is made yet: no API documentation or keys were available when this was built.

| Feature | Data today | Switch |
|---|---|---|
| Imagery under the map | USGS National Map imagery (public domain) | one setting, below |
| Vegetation near the wires | USDA NAIP photos, with their infrared band | add a provider module |
| Building types | hospitals and water plants from OpenStreetMap (218 sites) | drop in one file |

## Switching to Vexcel imagery

**Under the map.** Any XYZ tile service works. In `.env`:

```
HIGHWIRE_BASEMAP_URL=<Vexcel tile address with {z}, {x}, {y} and the key>
HIGHWIRE_BASEMAP_NAME=Vexcel
```

then `make imagery`. The address is read from the environment only and is never written to the cache or the metadata.

**For the vegetation measure.** Add `site_agent/vexcel.py` with the same functions as `site_agent/naip.py`
(`tile_for`, `tiles_for`, `cached_tile`; tile dicts with `img`, `bbox`, `center`, `photo_date`, `gsd_m`, `source`, and
`nir` when an infrared crop exists), register it in `site_agent/imagery.py` (`PROVIDERS`), and set
`HIGHWIRE_IMAGERY=vexcel`. `site_agent/greenery.py` needs no change: it uses the infrared crop when there is one and
a colour rule when there is not. The colour rule agrees with infrared on 82% of pixels across eight LA photos and
over-counts dark roofs and asphalt, so prefer imagery with an infrared band.

## Switching to CoStar buildings

Write the properties to `data/processed/costar/buildings.json` (git-ignored) as a JSON list:

```json
[{"id": "...", "name": "...", "property_type": "Office", "secondary_type": "", "lat": 34.05, "lon": -118.25}]
```

Only `property_type`, `lat` and `lon` are required. `backend/la/buildings.py` maps the type to a kind (hospital,
medical, data center, cold storage, school, residential, industrial, office, retail, other), assigns each property
to the nearest substation, and from then on every count, score and list includes it. The only code still to write is
the exporter that calls the CoStar API and saves that list.

Weights are in `backend/la/buildings.py` (`WEIGHTS`): hospital 10; emergency services and water 8; medical 6; data
center 5; cold storage and schools 4; residential, industrial and lodging 2; office, retail and other 1. They are a
judgement for triage, not a measure of people or dollars.

## Limits to say out loud

- Lines of 66 kV and above are modelled, not the feeders that serve each building. "Loses grid power" means the
  substation serving the area is cut off.
- Photos show the day they were flown and cover a line's most fire-prone stretches, not its whole length.
- Licensed data (CoStar, Vexcel, Matterport responses, tiles and derived tables) stays out of git: the cache folders
  under `data/processed/` are ignored.
