# Site agent (Gemini + aerial photos)

For a line above the fire alarm, the site agent shows the operator **what is on the ground** at the line's most
fire-prone tower and **who is downwind**. Gemini describes a USDA NAIP aerial photo; the data (LANDFIRE, Census, OSM)
supplies every number. Where the two disagree, the panel says so.

It is a self-contained folder. The rest of Rewire uses it in two places only:
- `GET /la/site/{line_id}?event=E3&i=10` (and `/la/site/{line_id}/photo.jpg`): the **Site agent** panel under
  "Lines above the fire alarm" in the LA Replay tab (click a line).
- `get_site_context(line_id)`: a tool for the co-pilot's `fire_agent`. It reads facts and any **cached** Gemini
  description; it never makes a live Gemini or network call inside the planning budget.

## Files

| File | What it does |
| --- | --- |
| `naip.py` | Finds the newest NAIP photo covering a point (Planetary Computer STAC, keyless) and saves a ~1.2 km JPEG crop to `data/processed/naip/<line>.jpg` |
| `facts.py` | Deterministic facts per line: most fire-prone tower (LANDFIRE receptivity x zone fuel), fuel mix along the line, vegetation height, and, for a replay hour, gust, humidity and the 10 km downwind cone (people, hospitals, water plants) |
| `agent.py` | One multimodal Gemini call (photo + facts) returning a fixed JSON schema, sanitizing, cross-checks, caching |
| `test_site_agent.py` | Offline tests with a fake Gemini |

## Rules

1. **No numbers from Gemini.** Its output is categorical (land cover, buildings, vegetation, access, confidence) plus a
   two-sentence summary. Digits in the summary are removed and the removal is noted.
2. **Never a physics input.** Nothing here changes a fire, storm or power-flow number.
3. **Disagreements are shown, not resolved.** Cross-checks flag: LANDFIRE says developed but the photo shows brush
   (the model may understate risk, as at the Hurst tower); LANDFIRE says brush but the photo shows built ground; homes in
   the photo but few Census people downwind; many Census people downwind but none in the photo.
4. **Works without a key.** With no `GEMINI_API_KEY`, the panel still shows the photo and the facts.

## Run

```bash
python -m site_agent.naip              # pre-fetch photos for every line that crosses the alarm in any replay (~70 KB each)
python -m site_agent.naip L29 L171     # or just these lines
python -m site_agent.agent L29 E3 100  # print facts + photo + Gemini's description for one line at one replay hour
python -m pytest site_agent -q
```

Photos and cached descriptions live in `data/processed/naip/` (git-ignored). A description is cached per line, photo
and model, so changing `GEMINI_MODEL` or the photo triggers a fresh call.

## Not done yet

- A hand-labelled set of ~30 photos to measure how often Gemini's land-cover call is right.
- The photo is centred on one tower; long lines cross several landscapes. A strip of photos along the line would cover more.
- `GEMINI_MODEL` defaults to `gemini-3.5-flash`, which has not been tried against the live API with a key.
