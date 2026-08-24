"""
Push TECO data into Home Assistant from inside the add-on.

When running as a Home Assistant add-on (with `homeassistant_api: true`), the
Supervisor injects a `SUPERVISOR_TOKEN` that grants access to the HA Core API.
This module uses it to:

  1. Feed the **Energy Dashboard** — import daily kWh + daily cost as long-term
     statistics via the `recorder/import_statistics` WebSocket command
     (statistic_ids `teco:energy_consumption` and `teco:energy_cost`).
     Gas, when the login has a Peoples Gas account, is imported alongside it as
     `teco:gas_consumption` (CCF, one point per billing period -- TECO publishes no
     daily gas readings) and `teco:gas_cost`.
  2. Create/refresh **sensor entities** (amount due, last bill cost/usage/$ per kWh,
     service period, account status, program flags -- plus `sensor.teco_gas_*` when
     there is a gas account) via the REST states API.

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
STAT_GAS = "teco:gas_consumption"
STAT_GAS_COST = "teco:gas_cost"

# TECO bills gas in therms, which HA's Energy Dashboard does not accept as a gas
# unit. The meter index itself is in CCF (hundred cubic feet) and CCF *is* a valid
# HA gas unit, so we publish the reading delta -- exact integers straight off the
# meter, no conversion factor invented on our side. (For reference, TECO's own
# therms/CCF heat-content ratio runs ~1.15.)
GAS_UNIT = "CCF"

# When true, set an existing grid source's cost to TECO's actual billed cost
# (replacing any static $/kWh price). Opt-in — it changes the user's energy config.
GRID_COST_FROM_TECO = os.environ.get("GRID_COST_FROM_TECO", "0") != "0"


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


def _build_gas_stats(data: dict, tz: ZoneInfo):
    """Return (usage_stats, cost_stats) for gas: one point per billing period.

    Gas has no daily readings -- the ViewBill page loads meterDataMonthlyUsage and
    no daily component at all -- so each bill contributes a single point covering
    its service period, stamped at the period start.
    """
    gas = data.get("gas") or {}
    bills = gas.get("bills") or []
    rows = []
    for b in bills:
        start = _as_date(b.get("service_period_start"))
        prev, curr = b.get("previous_reading"), b.get("current_reading")
        if start is None or prev is None or curr is None:
            continue
        ccf = float(curr) - float(prev)
        if ccf < 0:          # meter rollover -- skip rather than emit a negative
            continue
        rows.append((start, ccf, b.get("cost")))
    rows.sort(key=lambda r: r[0])

    usage, cost = [], []
    usum = csum = 0.0
    for d, ccf, amount in rows:
        stamp = datetime(d.year, d.month, d.day, tzinfo=tz).isoformat()
        usum += ccf
        usage.append({"start": stamp, "state": ccf, "sum": round(usum, 3)})
        if amount is not None:
            csum += float(amount)
            cost.append({"start": stamp, "state": float(amount), "sum": round(csum, 4)})
    return usage, cost


async def _ha_timezone(session: aiohttp.ClientSession) -> ZoneInfo:
    try:
        async with session.get(f"{CORE_REST}/config", headers=_headers()) as r:
            tz = (await r.json()).get("time_zone", "UTC")
            return ZoneInfo(tz)
    except Exception:
        return ZoneInfo("UTC")


async def _import_statistics(session, data, tz, log):
    energy, cost = _build_stats(data, tz)
    gas, gas_cost = _build_gas_stats(data, tz)
    if not energy and not gas:
        log.warning("no statistics to import (no daily electric usage, no gas bills)")
        return
    jobs = []
    if energy:
        jobs.append(({"has_mean": False, "has_sum": True, "name": "TECO Energy",
                      "source": "teco", "statistic_id": STAT_ENERGY,
                      "unit_of_measurement": "kWh"}, energy))
    if cost:
        jobs.append(({"has_mean": False, "has_sum": True, "name": "TECO Energy Cost",
                      "source": "teco", "statistic_id": STAT_COST,
                      "unit_of_measurement": "USD"}, cost))
    if gas:
        jobs.append(({"has_mean": False, "has_sum": True, "name": "TECO Gas",
                      "source": "teco", "statistic_id": STAT_GAS,
                      "unit_of_measurement": GAS_UNIT}, gas))
    if gas_cost:
        jobs.append(({"has_mean": False, "has_sum": True, "name": "TECO Gas Cost",
                      "source": "teco", "statistic_id": STAT_GAS_COST,
                      "unit_of_measurement": "USD"}, gas_cost))
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
    # --- gas (Peoples Gas), when the login has a gas account too ------------- #
    gas = data.get("gas") or {}
    if gas:
        gcb = gas.get("current_bill") or {}
        gbills = gas.get("bills") or []
        glast = gbills[0] if gbills else {}
        # TECO reports gas usage in therms; the meter index is CCF.
        therms = glast.get("kwh_used")          # parse_meter_data stores TotalUsed here
        prev, curr = glast.get("previous_reading"), glast.get("current_reading")
        ccf = (float(curr) - float(prev)) if (prev is not None and curr is not None) else None
        gcost = glast.get("cost")
        rate = round(gcost / therms, 5) if (gcost and therms) else None
        out += [
            s("sensor.teco_gas_amount_due", gcb.get("total_amount_due"),
              unit_of_measurement="USD", device_class="monetary",
              friendly_name="TECO Gas Amount Due", icon="mdi:cash"),
            s("sensor.teco_gas_due_date", gcb.get("due_date"),
              device_class="date", friendly_name="TECO Gas Payment Due Date",
              icon="mdi:calendar-clock"),
            s("sensor.teco_gas_last_bill_cost", gcost,
              unit_of_measurement="USD", device_class="monetary",
              friendly_name="TECO Gas Last Bill Cost", icon="mdi:receipt-text",
              service_period_start=glast.get("service_period_start"),
              service_period_end=glast.get("service_period_end"),
              previous_reading=prev, current_reading=curr),
            s("sensor.teco_gas_last_bill_usage", therms,
              unit_of_measurement="therms", state_class="total",
              friendly_name="TECO Gas Last Bill Usage", icon="mdi:fire"),
            s("sensor.teco_gas_last_bill_volume", ccf,
              unit_of_measurement=GAS_UNIT, device_class="gas", state_class="total",
              friendly_name="TECO Gas Last Bill Volume", icon="mdi:meter-gas"),
            s("sensor.teco_gas_last_bill_rate", rate,
              unit_of_measurement="USD/therm", friendly_name="TECO Gas Last Bill $/therm",
              icon="mdi:cash-multiple"),
            s("sensor.teco_gas_service_period_start", glast.get("service_period_start"),
              device_class="date", friendly_name="TECO Gas Service Period Start",
              icon="mdi:calendar-start"),
            s("sensor.teco_gas_service_period_end", glast.get("service_period_end"),
              device_class="date", friendly_name="TECO Gas Service Period End",
              icon="mdi:calendar-end"),
            s("sensor.teco_gas_service_days", glast.get("service_days"),
              unit_of_measurement="d", friendly_name="TECO Gas Service Period Days",
              icon="mdi:calendar-range"),
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


def _grid_template() -> dict:
    """A complete HA 'grid' energy source (flat schema) fed by TECO + its cost."""
    return {
        "type": "grid",
        "stat_energy_from": STAT_ENERGY,
        "stat_energy_to": None,
        "stat_cost": STAT_COST,
        "stat_compensation": None,
        "entity_energy_price": None,
        "number_energy_price": None,
        "entity_energy_price_export": None,
        "number_energy_price_export": None,
        "cost_adjustment_day": 0.0,
    }


async def configure_energy(log, has_gas: bool = False) -> bool:
    """Wire TECO into the HA Energy Dashboard — SAFELY, without double-counting.

    HA's grid source schema is flat: each grid source has a `stat_energy_from`
    (consumption) and `stat_cost`. Behavior:
      - TECO already the consumption source -> ensure its stat_cost = teco:energy_cost
      - NO grid consumption configured at all -> add a TECO grid source (+cost)
      - a *different* grid source already exists (e.g. a panel/CT monitor) ->
        leave it ALONE (adding TECO would double-count) and just log that TECO's
        statistics are available to add manually.
    """
    if not available():
        return True
    try:
        async with aiohttp.ClientSession() as s:
            async with s.ws_connect(CORE_WS, heartbeat=30) as ws:
                await ws.receive_json()  # auth_required
                await ws.send_json({"type": "auth", "access_token": SUPERVISOR_TOKEN})
                if (await ws.receive_json()).get("type") != "auth_ok":
                    return False
                await ws.send_json({"id": 1, "type": "energy/get_prefs"})
                prefs = (await ws.receive_json()).get("result") or {}
                sources = prefs.get("energy_sources", [])
                grids = [x for x in sources if x.get("type") == "grid"]

                # Gas is additive and can't double-count a grid source, so it is
                # safe to add whenever we actually have gas statistics.
                changed_gas = False
                if has_gas:
                    gas_srcs = [x for x in sources if x.get("type") == "gas"]
                    mine = next((g for g in gas_srcs
                                 if g.get("stat_energy_from") == STAT_GAS), None)
                    if mine is None:
                        sources.append({
                            "type": "gas",
                            "stat_energy_from": STAT_GAS,
                            "stat_cost": STAT_GAS_COST,
                            "entity_energy_price": None,
                            "number_energy_price": None,
                        })
                        changed_gas = True
                        log.info("energy: added TECO gas source (%s, %s)",
                                 STAT_GAS, GAS_UNIT)
                    elif mine.get("stat_cost") != STAT_GAS_COST:
                        mine["stat_cost"] = STAT_GAS_COST
                        changed_gas = True

                ours = next((g for g in grids if g.get("stat_energy_from") == STAT_ENERGY), None)
                changed = False
                if ours is not None:
                    if ours.get("stat_cost") != STAT_COST:
                        ours["stat_cost"] = STAT_COST
                        changed = True
                elif not any(g.get("stat_energy_from") for g in grids):
                    sources.append(_grid_template())
                    changed = True
                elif GRID_COST_FROM_TECO:
                    # use TECO's actual billed cost on the existing grid source
                    g = next(x for x in grids if x.get("stat_energy_from"))
                    if (g.get("stat_cost") != STAT_COST
                            or g.get("number_energy_price") is not None
                            or g.get("entity_energy_price") is not None):
                        g["stat_cost"] = STAT_COST
                        g["number_energy_price"] = None
                        g["entity_energy_price"] = None
                        changed = True
                        log.info("energy: set grid source %s cost -> teco:energy_cost "
                                 "(actual billed cost; replaced any static price)",
                                 g.get("stat_energy_from"))
                else:
                    other = next((g.get("stat_energy_from") for g in grids
                                  if g.get("stat_energy_from")), "?")
                    log.info("energy: a grid source (%s) is already configured; leaving it "
                             "alone. Enable 'grid_cost_from_teco' to use TECO's actual cost, "
                             "or add the teco statistics manually.", other)

                if changed or changed_gas:
                    await ws.send_json({
                        "id": 2, "type": "energy/save_prefs",
                        "energy_sources": sources,
                        "device_consumption": prefs.get("device_consumption", []),
                    })
                    resp2 = await ws.receive_json()
                    if resp2.get("success"):
                        log.info("energy dashboard configured with TECO consumption + cost")
                        return True
                    log.error("energy save_prefs failed: %s",
                              str(resp2.get("error") or resp2)[:400])
                    return False
                log.info("energy dashboard already wired for TECO")
                return True
    except Exception:  # noqa: BLE001
        log.exception("configure_energy failed")
        return False


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
