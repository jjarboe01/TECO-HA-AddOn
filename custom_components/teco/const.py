"""Constants for the TECO integration."""
from __future__ import annotations

from datetime import timedelta

DOMAIN = "teco"

CONF_URL = "url"
CONF_TOKEN = "token"

DEFAULT_URL = "http://homeassistant.local:8089"
# utility data updates ~daily; poll a few times/day, not minutes.
DEFAULT_SCAN_INTERVAL = timedelta(hours=6)

# external statistics ids (Energy Dashboard)
STAT_ENERGY = f"{DOMAIN}:energy_consumption"
STAT_COST = f"{DOMAIN}:energy_cost"
