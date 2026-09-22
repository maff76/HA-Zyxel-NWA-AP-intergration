from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .cgi import ZyxelCgiClient
from .const import CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, PLATFORMS
from .coordinator import ZyxelNwaCoordinator


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)



async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    host = entry.data[CONF_HOST]
    username = entry.options.get(CONF_USERNAME, entry.data.get(CONF_USERNAME, "admin"))
    password = entry.options.get(CONF_PASSWORD, entry.data.get(CONF_PASSWORD, ""))

    cgi = ZyxelCgiClient(hass, host, username, password)
    coordinator = ZyxelNwaCoordinator(
        hass, host, cgi, entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    )
    await coordinator.async_load_traffic_totals()
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    coordinator.async_start_traffic_polling(entry)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = entry.runtime_data
    await coordinator.async_stop_traffic_polling()
    await coordinator._async_save_traffic_totals(force=True)
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    await coordinator.cgi_client.async_close()
    return unloaded
