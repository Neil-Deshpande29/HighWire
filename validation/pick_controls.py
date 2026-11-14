"""Pick calm control weeks: 7-day windows (Mon 00:00 local) in 2019-2024, Oct-Feb (Santa Ana season, so the
false-alarm test is not trivially easy), with no LOX Red Flag Warning in any zone touching the bbox,
at least 14 days from every event window. Seeded for reproducibility. Prints YAML to paste into events.yaml."""
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
rf = json.loads((ROOT / "data/processed/redflag_la.json").read_text())
events = yaml.safe_load((ROOT / "validation/events.yaml").read_text())["events"]
warn = [(datetime.fromisoformat(e["issue"].replace("Z", "+00:00")), datetime.fromisoformat(e["expire"].replace("Z", "+00:00"))) for e in rf["events"]]
busy = [(datetime.fromisoformat(e["start"]).astimezone(timezone.utc) - timedelta(days=14),
         datetime.fromisoformat(e["end"]).astimezone(timezone.utc) + timedelta(days=14)) for e in events if e["kind"] != "control"]
cands = []
d = datetime(2019, 1, 7, 8, tzinfo=timezone.utc)  # a Monday, 00:00 PST
while d.year < 2025:
    s, e = d, d + timedelta(days=7)
    if d.month in (10, 11, 12, 1, 2) and not any(a < e and b > s for a, b in warn) and not any(a < e and b > s for a, b in busy):
        cands.append(d)
    d += timedelta(days=7)
random.seed(7)
pick = sorted(random.sample(cands, 3))
print(f"# {len(cands)} candidate calm weeks; picked with random.seed(7)")
for i, p in enumerate(pick, 1):
    loc = p - timedelta(hours=8)
    print(f"  - {{id: C{i}, kind: control, start: '{loc:%Y-%m-%d}T00:00:00-08:00', end: '{loc + timedelta(days=7):%Y-%m-%d}T00:00:00-08:00'}}")
