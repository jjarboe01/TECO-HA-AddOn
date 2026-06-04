# TECO Billing add-on

Tampa Electric (TECO) billing & usage in Home Assistant, with a sidebar dashboard
and a persistent, never-purged bill archive.

- **Configuration tab:** TECO username + password (+ backfill depth, session TTL)
- **Sidebar panel:** billing dashboard — bills with service period, kWh, cost,
  $/kWh, meter reads; per-bill charts; daily usage; CSV export; per-bill re-assemble
- **Archive:** every bill is cached in `/data/cache` and never deleted

See `DOCS.md` for full instructions. Built on the same engine as the standalone
Docker sidecar (`../../sidecar`).
