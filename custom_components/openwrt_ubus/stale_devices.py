"""Removal of client devices that have not been seen on the router for a long time.

Every client that ever connected gets a device (and entities) in Home Assistant. Without
cleanup they stay forever as "unavailable". The time each MAC was last seen is persisted
in a Store, so it survives Home Assistant restarts.
"""

from __future__ import annotations

from collections.abc import Iterable
import logging
import re
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.storage import Store

from .const import CONF_STALE_DEVICE_DAYS, DEFAULT_STALE_DEVICE_DAYS, DOMAIN, entry_setting

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
SAVE_DELAY = 120  # seconds; saves are batched
_MAC_RE = re.compile(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$", re.IGNORECASE)


def _storage_key(entry_id: str) -> str:
    return f"{DOMAIN}.last_seen.{entry_id}"


async def async_remove_storage(hass: HomeAssistant, entry_id: str) -> None:
    """Delete the stored data of a removed config entry."""
    await Store(hass, STORAGE_VERSION, _storage_key(entry_id)).async_remove()


class StaleDeviceCleaner:
    """Tracks when each client was last seen and removes devices that vanished."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._hass = hass
        self._entry = entry
        self._store: Store[dict[str, float]] = Store(hass, STORAGE_VERSION, _storage_key(entry.entry_id))
        self._last_seen: dict[str, float] = {}
        # Set after the first successful poll; without it a long router outage would make
        # every device look "old" and the first cleanup would remove all of them.
        self._has_data = False

    async def async_load(self) -> None:
        """Load the persisted timestamps."""
        data = await self._store.async_load() or {}
        self._last_seen = {str(mac).upper(): float(ts) for mac, ts in data.items()}

    async def async_flush(self) -> None:
        """Write pending timestamps to disk (called on unload)."""
        await self._store.async_save(self._data_to_save())

    @callback
    def _data_to_save(self) -> dict[str, float]:
        return dict(self._last_seen)

    @callback
    def mark_seen(self, macs: Iterable[str]) -> None:
        """Record that these clients are connected right now (after a successful poll)."""
        self._has_data = True
        now = time.time()
        changed = False
        for mac in macs:
            if mac:
                self._last_seen[mac.upper()] = now
                changed = True
        if changed:
            self._store.async_delay_save(self._data_to_save, SAVE_DELAY)

    @callback
    def async_cleanup(self) -> list[str]:
        """Remove client devices not seen for the configured number of days.

        Returns the MAC addresses of the removed devices.
        """
        days = entry_setting(self._entry, CONF_STALE_DEVICE_DAYS, DEFAULT_STALE_DEVICE_DAYS)
        if not days or days <= 0 or not self._has_data:
            return []

        now = time.time()
        limit = days * 86400
        registry = dr.async_get(self._hass)
        removed: list[str] = []
        changed = False

        for device in dr.async_entries_for_config_entry(registry, self._entry.entry_id):
            # Client devices are identified by their MAC; hubs (router, AP, MWAN3...) are not
            mac = next(
                (ident[1] for ident in device.identifiers if ident[0] == DOMAIN and _MAC_RE.match(ident[1])),
                None,
            )
            if mac is None:
                continue
            mac = mac.upper()
            last_seen = self._last_seen.get(mac)
            if last_seen is None:
                # Never recorded (existing device after an upgrade): count from now on
                self._last_seen[mac] = now
                changed = True
                continue
            if now - last_seen > limit:
                _LOGGER.info(
                    "Removing device %s (%s): not seen for more than %d days",
                    device.name_by_user or device.name,
                    mac,
                    days,
                )
                registry.async_remove_device(device.id)
                self._last_seen.pop(mac, None)
                removed.append(mac)
                changed = True

        if changed:
            self._store.async_delay_save(self._data_to_save, SAVE_DELAY)
        return removed
