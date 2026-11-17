"""Support for OpenWrt buttons via ubus."""

from __future__ import annotations

import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    CONF_ENABLE_SERVICE_CONTROLS,
    CONF_ENABLE_DEVICE_KICK_BUTTONS,
    CONF_ENABLE_REBOOT_BUTTON,
    DEFAULT_ENABLE_SERVICE_CONTROLS,
    DEFAULT_ENABLE_DEVICE_KICK_BUTTONS,
    DEFAULT_ENABLE_REBOOT_BUTTON,
)
from .buttons import service_button, device_kick_button, reboot_button

_LOGGER = logging.getLogger(__name__)

# Button modules configuration
BUTTON_MODULES = [
    {
        "module": service_button,
        "config_key": CONF_ENABLE_SERVICE_CONTROLS,
        "default": DEFAULT_ENABLE_SERVICE_CONTROLS,
        "name": "service_button",
    },
    {
        "module": device_kick_button,
        "config_key": CONF_ENABLE_DEVICE_KICK_BUTTONS,
        "default": DEFAULT_ENABLE_DEVICE_KICK_BUTTONS,
        "name": "device_kick_button",
    },
    {
        "module": reboot_button,
        "config_key": CONF_ENABLE_REBOOT_BUTTON,
        "default": DEFAULT_ENABLE_REBOOT_BUTTON,
        "name": "reboot_button",
    },
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up OpenWrt buttons from a config entry."""
    _LOGGER.info("Setting up OpenWrt buttons")

    # The modules are independent, so set them up concurrently (each does its own first refresh)
    async def _setup_module(button_config: dict):
        module = button_config["module"]
        module_name = button_config["name"]

        # Priority: options > data > default
        enabled = entry.options.get(
            button_config["config_key"], entry.data.get(button_config["config_key"], button_config["default"])
        )
        if not enabled:
            _LOGGER.info("Button module %s is disabled in configuration", module_name)
            return None
        if not hasattr(module, "async_setup_entry"):
            _LOGGER.warning("Button module %s has no async_setup_entry function", module_name)
            return None

        try:
            _LOGGER.debug("Loading button module: %s", module_name)
            coordinator = await module.async_setup_entry(hass, entry, async_add_entities)
        except Exception as exc:  # noqa: BLE001 - one broken module must not break the others
            _LOGGER.error("Error setting up button module %s: %s", module_name, exc)
            return None

        # device_kick_button registers its own coordinator and returns nothing
        if coordinator:
            _LOGGER.info("Successfully loaded button module: %s", module_name)
        return coordinator

    results = await asyncio.gather(*(_setup_module(cfg) for cfg in BUTTON_MODULES))
    coordinators = [c for c in results if c]

    _LOGGER.info("Completed loading of %d button modules", len(coordinators))

    # Remember coordinators so they are shut down on unload
    entry.runtime_data.coordinators.extend(coordinators)
