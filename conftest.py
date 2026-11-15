"""Tests run offline and never use this machine's keys.

backend/llm.py and backend/la/vexcel.py read the repo's .env when they are imported, and they leave a variable alone
when it is already set. Setting the keys to empty here, before any test module is imported, keeps a real key in .env
out of every test; a test that needs the keyed path sets a dummy key itself and fakes the service behind it."""
import os

for _name in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "VEXCEL_API_KEY"):
    os.environ[_name] = ""
os.environ["LIGHTS_PORT"] = "off"  # and no test ever opens the LED strip's serial port
