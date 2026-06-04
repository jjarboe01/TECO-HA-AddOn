"""
Push TECO data into Home Assistant from inside the add-on.

When running as a Home Assistant add-on (with `homeassistant_api: true`), the
Supervisor injects a `SUPERVISOR_TOKEN` that grants access to the HA Core API.
This module uses it to:

  1. Feed the **Energy Dashboard** — import daily kWh + daily cost as long-term
     statistics via the `recorder/import_statistics` WebSocket command
     (statistic_ids `teco:energy_consumption` and `teco:energy_cost`).
  2. Create/refresh **sensor entities** (amount due, last bill cost/usage/$ per kWh,
     service period, account status, program flags) via the REST states API.

No MQTT and no custom integration required. When SUPERVISOR_TOKEN is absent
(plain Docker / standalone), `available()` is False and publishing is skipped.
"""
from __future__ import annotations

import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

import aiohttp

SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN")
CORE_REST = "http://supervisor/core/api"
CORE_WS = "ws://supervisor/core/websocket"

STAT_ENERGY = "teco:energy_consumption"
STAT_COST = "teco:energy_cost"


def available() -> bool:
    return bool(SUPERVISOR_TOKEN)


def _headers() -> dict:
    return {"Authorization": f"Bearer {SUPERVISOR_TOKEN}",
            "Content-Type": "application/json"}


def _as_date(s) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def _rate_for(d: date, bills: list[dict]) -> float | None:
    for b in bills:
        s, e = _as_date(b.get("service_period_start")), _as_date(b.get("service_period_end"))
        if s and e and s <= d <= e and b.get("cost") and b.get("kwh_used"):
            return b["cost"] / b["kwh_used"]
    return None


def _build_stats(data: dict, tz: ZoneInfo):
    """Return (energy_stats, cost_stats) as lists of {start, state, sum}."""
    daily = data.get("daily_usage") or []
    bills = data.get("bills") or []
    points = sorted(
        ((_as_date(d.get("date")), d.get("kwh")) for d in daily),
        key=lambda x: (x[0] or date.min),
    )
    energy, cost = [], []
    esum = csum = 0.0
    for d, kwh in points:
        if d is None or kwh is None:
            continue
        start = datetime(d.year, d.month, d.day, tzinfo=tz).isoformat()
        esum += float(kwh)
        energy.append({"start": start, "state": float(kwh), "sum": round(esum, 3)})
        rate = _rate_for(d, bills)
        if rate is not None:
            day_cost = round(float(kwh) * rate, 4)
            csum += day_cost
            cost.append({"start": start, "state": day_cost, "sum": round(csum, 4)})
    return energy, cost


async def _ha_timezone(session: aiohttp.ClientSession) -> ZoneInfo:
    try:
        async with session.get(f"{CORE_REST}/config", headers=_headers()) as r:
            tz = (await r.json()).get("time_zone", "UTC")
            return ZoneInfo(tz)
    except Exception:
        return ZoneInfo("UTC")


async def _import_statistics(session, data, tz, log):
    energy, cost = _build_stats(data, tz)
    if not energy:
        return
    jobs = [
        ({"has_mean": False, "has_sum": True, "name": "TECO Energy",
          "source": "teco", "statistic_id": STAT_ENERGY,
          "unit_of_measurement": "kWh"}, energy),
    ]
    if cost:
        jobs.append(({"has_mean": False, "has_sum": True, "name": "TECO Energy Cost",
                      "source": "teco", "statistic_id": STAT_COST,
                      "unit_of_measurement": "USD"}, cost))
    async with session.ws_connect(CORE_WS, heartbeat=30) as ws:
        await ws.receive_json()                                   # auth_required
        await ws.send_json({"type": "auth", "access_token": SUPERVISOR_TOKEN})
        auth = await ws.receive_json()
        if auth.get("type") != "auth_ok":
            log.error("HA websocket auth failed: %s", auth)
            return
        mid = 1
        for metadata, stats in jobs:
            await ws.send_json({"id": mid, "type": "recorder/import_statistics",
                                "metadata": metadata, "stats": stats})
            resp = await ws.receive_json()
            ok = resp.get("success")
            log.info("import_statistics %s: %s (%d points)",
                     metadata["statistic_id"], "ok" if ok else resp, len(stats))
            mid += 1


def _sensor_payloads(data: dict) -> list[tuple[str, object, dict]]:
    cb = data.get("current_bill") or {}
    bills = data.get("bills") or []
    last = bills[0] if bills else {}
    acct = data.get("account") or {}
    flags = data.get("flags") or {}
    dev = {"identifiers": ["teco"], "name": "TECO (Tampa Electric)",
           "manufacturer": "Tampa Electric"}

    def s(eid, state, **attrs):
        attrs.setdefault("attribution", "Data provided by Tampa Electric (TECO)")
        return (eid, state, attrs)

    out = [
        s("sensor.teco_amount_due", cb.get("total_amount_due"),
          unit_of_measurement="USD", device_class="monetary",
          friendly_name="TECO Amount Due", icon="mdi:cash"),
        s("sensor.teco_due_date", cb.get("due_date"),
          device_class="date", friendly_name="TECO Payment Due Date",
          icon="mdi:calendar-clock"),
        s("sensor.teco_last_bill_cost", last.get("cost"),
          unit_of_measurement="USD", device_class="monetary",
          friendly_name="TECO Last Bill Cost", icon="mdi:receipt-text",
          service_period_start=last.get("service_period_start"),
          service_period_end=last.get("service_period_end"),
          previous_reading=last.get("previous_reading"),
          current_reading=last.get("current_reading")),
        s("sensor.teco_last_bill_usage", last.get("kwh_used"),
          unit_of_measurement="kWh", device_class="energy",
          state_class="total", friendly_name="TECO Last Bill Usage"),
        s("sensor.teco_last_bill_rate", last.get("cost_per_kwh"),
          unit_of_measurement="USD/kWh", friendly_name="TECO Last Bill $/kWh",
          icon="mdi:cash-multiple"),
        s("sensor.teco_service_period_start", last.get("service_period_start"),
          device_class="date", friendly_name="TECO Service Period Start",
          icon="mdi:calendar-start"),
        s("sensor.teco_service_period_end", last.get("service_period_end"),
          device_class="date", friendly_name="TECO Service Period End",
          icon="mdi:calendar-end"),
        s("sensor.teco_service_days", last.get("service_days"),
          unit_of_measurement="d", friendly_name="TECO Service Period Days",
          icon="mdi:calendar-range"),
        s("sensor.teco_account_status", acct.get("status") or "unknown",
          friendly_name="TECO Account Status", icon="mdi:account-check"),
        s("sensor.teco_last_updated", data.get("fetched_at"),
          device_class="timestamp", friendly_name="TECO Last Updated"),
    ]
    flag_meta = {
        "paperless": ("Paperless Billing", "mdi:file-document-outline"),
        "autopay": ("Autopay", "mdi:bank-transfer"),
        "budget_billing": ("Budget Billing", "mdi:scale-balance"),
        "sun_select": ("SunSelect", "mdi:solar-power"),
        "energy_planner": ("Energy Planner", "mdi:calendar-clock"),
        "prime_time_plus": ("Prime Time Plus", "mdi:clock-star-four-points"),
        "power_updates": ("Power Updates", "mdi:transmission-tower"),
    }
    for key, (name, icon) in flag_meta.items():
        val = flags.get(key)
        out.append(s(f"binary_sensor.teco_{key}",
                     "on" if val else "off" if val is not None else "unavailable",
                     friendly_name=f"TECO {name}", icon=icon))
    # drop entities whose state is None (don't publish empty)
    return [(eid, ("" if st is None else st), at) for eid, st, at in out if st is not None]


async def _update_sensors(session, data, log, quiet=False):
    n = 0
    for eid, state, attrs in _sensor_payloads(data):
        try:
            async with session.post(f"{CORE_REST}/states/{eid}",
                                    headers=_headers(),
                                    json={"state": str(state), "attributes": attrs}) as r:
                if r.status in (200, 201):
                    n += 1
                else:
                    log.warning("state %s -> HTTP %s", eid, r.status)
        except Exception as e:  # noqa: BLE001
            log.warning("state %s failed: %s", eid, e)
    (log.debug if quiet else log.info)("updated %d TECO sensor entities", n)


async def publish(data: dict, log) -> None:
    """Push statistics + sensor states into Home Assistant. No-op if unavailable."""
    if not available():
        return
    async with aiohttp.ClientSession() as session:
        tz = await _ha_timezone(session)
        try:
            await _import_statistics(session, data, tz, log)
        except Exception:  # noqa: BLE001
            log.exception("import_statistics failed")
        try:
            await _update_sensors(session, data, log)
        except Exception:  # noqa: BLE001
            log.exception("sensor update failed")


async def configure_energy(log) -> None:
    """Attach teco:energy_cost to the teco:energy_consumption grid source in the HA
    Energy Dashboard, so it shows $ alongside kWh. Non-destructive:
      - if our consumption stat is already a grid source -> just set its cost stat
      - else if no grid consumption is configured yet -> add ours (+cost)
      - else (the user already has a different grid source) -> leave it alone
    """
    if not available():
        return
    try:
        async with aiohttp.ClientSession() as s:
            async with s.ws_connect(CORE_WS, heartbeat=30) as ws:
                await ws.receive_json()  # auth_required
                await ws.send_json({"type": "auth", "access_token": SUPERVISOR_TOKEN})
                if (await ws.receive_json()).get("type") != "auth_ok":
                    return
                await ws.send_json({"id": 1, "type": "energy/get_prefs"})
                resp = await ws.receive_json()
                prefs = resp.get("result") or {}
                sources = prefs.get("energy_sources", [])
                ours = {"stat_energy_from": STAT_ENERGY, "stat_cost": STAT_COST,
                        "entity_energy_price": None, "number_energy_price": None}
                grid = next((x for x in sources if x.get("type") == "grid"), None)
                changed = False
                if grid is None:
                    sources.append({"type": "grid", "flow_from": [ours], "flow_to": [],
                                    "cost_adjustment_day": 0.0})
                    changed = True
                else:
                    ff = grid.setdefault("flow_from", [])
                    existing = next((f for f in ff if f.get("stat_energy_from") == STAT_ENERGY), None)
                    if existing:
                        if existing.get("stat_cost") != STAT_COST:
                            existing.update({"stat_cost": STAT_COST,
                                             "entity_energy_price": None,
                                             "number_energy_price": None})
                            changed = True
                    elif not ff:
                        ff.append(ours)
                        changed = True
                    else:
                        log.info("energy: existing grid source found; leaving it alone "
                                 "(add 'teco:energy_consumption' manually if you want TECO instead)")
                if changed:
                    prefs["energy_sources"] = sources
                    prefs.setdefault("device_consumption", prefs.get("device_consumption", []))
                    await ws.send_json({"id": 2, "type": "energy/save_prefs", **prefs})
                    ok = (await ws.receive_json()).get("success")
                    log.info("energy dashboard %s with TECO consumption + cost",
                             "configured" if ok else "save FAILED")
                else:
                    log.info("energy dashboard already has TECO cost wired")
    except Exception:  # noqa: BLE001
        log.exception("configure_energy failed")


async def publish_sensors(data: dict, log) -> None:
    """Re-post only the sensor states (cheap heartbeat — keeps entities alive and
    recovers them quickly after an HA restart). No statistics, no TECO fetch."""
    if not available() or not data:
        return
    async with aiohttp.ClientSession() as session:
        try:
            await _update_sensors(session, data, log, quiet=True)
        except Exception:  # noqa: BLE001
            log.exception("sensor heartbeat failed")
