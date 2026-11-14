PY ?= .venv/bin/python
FETCH = cd data/fetch && ../../$(PY)

.PHONY: data grid calibrate validate test dev all clean-results

# 1. Download + process every public source (idempotent; skips files already present).
data:
	$(FETCH) fetch_terrain.py
	$(FETCH) fetch_osm.py
	$(FETCH) fetch_weather_grid.py events
	$(FETCH) fetch_weather_grid.py series
	$(FETCH) fetch_weather_grid.py series_precip
	$(FETCH) fetch_station_points.py
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
	$(PY) -c "import sys; sys.path.insert(0,'.'); from backend.la import data as D; import os; f=D.P/'outages_la_hourly.csv.gz'; f.unlink(missing_ok=True); D.outages()"

# 3. Fit gust correction, load model, fire thresholds (TRAINING events), storm-damage model (2019-2021), station nudging.
calibrate:
	cd validation && ../$(PY) calibrate.py

# 4. Run V1-V7 and write validation/REPORT.md.
validate:
	cd validation && ../$(PY) run_all.py

test:
	$(PY) -m pytest backend -q

dev:
	./run.sh

all: data grid calibrate validate

clean-results:
	rm -f validation/results/*.json validation/results/*.png validation/results/*.csv
