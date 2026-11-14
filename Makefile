PY ?= .venv/bin/python
FETCH = cd data/fetch && ../../$(PY)

.PHONY: data grid calibrate validate forecast naip site imagery trees costar test dev all clean-results

# 1. Download + process every public source (idempotent; skips files already present).
data:
	$(FETCH) fetch_terrain.py
	$(FETCH) fetch_osm.py
	$(FETCH) fetch_weather_grid.py events
	$(FETCH) fetch_weather_grid.py series
	$(FETCH) fetch_weather_grid.py series_precip
	$(FETCH) fetch_station_points.py
	$(FETCH) fetch_forecast_runs.py events
	$(FETCH) fetch_forecast_runs.py series
	$(FETCH) fetch_asos.py
	$(FETCH) fetch_eia930.py
	$(FETCH) fetch_eaglei.py
	$(FETCH) fetch_fires.py
	$(FETCH) fetch_redflag.py
	$(FETCH) fetch_eia860.py
	$(FETCH) fetch_hifld.py
	$(FETCH) fetch_census.py

# 2. Build the LA grid model and run the sanity / plan-to-peak step.
grid:
	$(PY) data/build_grid.py
	$(PY) data/sanity_grid.py
	$(FETCH) fetch_landfire.py   # needs the tower list, so it runs after the grid build
	$(PY) -c "import sys; sys.path.insert(0,'.'); from backend.la import data as D; import os; f=D.P/'outages_la_hourly.csv.gz'; f.unlink(missing_ok=True); D.outages()"

# 3. Fit gust correction, load model, fire thresholds (TRAINING events), storm-damage model (2019-2021), station nudging.
calibrate:
	cd validation && ../$(PY) calibrate.py

# 4. Run V1-V7 and write validation/REPORT.md.
validate:
	cd validation && ../$(PY) run_all.py

# 5. Live prediction: pull the current Open-Meteo forecast and run every calibrated model over the next ~3 days.
forecast:
	$(PY) -m backend.la.forecast

# 6. Gemini site agent: aerial photos (up to 4 per line that ever crosses the fire alarm; ~70 KB each, git-ignored),
#    then one Gemini description per line (needs GEMINI_API_KEY; without it only the photos are fetched), so the
#    co-pilot agents can read cached site context.
naip:
	$(PY) -m site_agent.naip

site: naip
	$(PY) -m site_agent.agent --batch

# 7. Aerial picture under the map (git-ignored). With VEXCEL_API_KEY in .env it comes from Vexcel, with the public
#    USGS picture where the key has no coverage; without a key it is all USGS.
imagery:
	$(PY) -m backend.la.basemap

# 8. Trees near the wires of every line that reaches the fire alarm, counted on Vexcel imagery (needs VEXCEL_API_KEY).
trees:
	$(PY) -m backend.la.trees

# 9. Load the CoStar data extract (from the track Discord, /costar-keys):  make costar FILE=/path/to/the/extract
costar:
	$(PY) -m backend.la.costar_import "$(FILE)"

test:
	$(PY) -m pytest backend site_agent -q

dev:
	./run.sh

all: data grid calibrate validate

clean-results:
	rm -f validation/results/*.json validation/results/*.png validation/results/*.csv
