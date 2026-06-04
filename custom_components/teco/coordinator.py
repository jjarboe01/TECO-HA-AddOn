"""Data update coordinator for TECO."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import TecoClient, TecoSidecarError
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN
from .statistics import async_import_statistics

_LOGGER = logging.getLogger(__name__)


class TecoCoordinator(DataUpdateCoordinator[dict]):
    """Polls the sidecar and pushes Energy Dashboard statistics."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: TecoClient):
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=DEFAULT_SCAN_INTERVAL,
        )
        self.entry = entry
        self.client = client

    async def _async_update_data(self) -> dict:
        try:
            data = await self.client.get_data()
        except TecoSidecarError as e:
            raise UpdateFailed(str(e)) from e

        # feed long-term statistics (daily kWh + distributed daily cost)
        try:
            await async_import_statistics(self.hass, data)
        except Exception:  # noqa: BLE001  -- never let stats break entity updates
            _LOGGER.exception("failed importing TECO statistics")
        return data
