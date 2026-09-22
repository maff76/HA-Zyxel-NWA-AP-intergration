"""Firmware update availability for Zyxel NWA access points."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re

from homeassistant.components.update import UpdateDeviceClass, UpdateEntity
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity import EntityCategory

from .entity import ZyxelEntity

DOWNLOAD_URL = "https://www.zyxel.com/global/en/support/download?model=nwa210ax"
CHECK_INTERVAL = timedelta(hours=6)


def _normalise(version: str) -> tuple[int, int, int]:
    """Convert Zyxel versions such as V7.12(ABTD.0) / 7.12(ABTD.0)C0 to comparable numbers."""
    match = re.search(r"V?(\d+)\.(\d+)\([A-Z0-9]+\.(\d+)\)(?:C\d+)?", version or "", re.I)
    if not match:
        return (0, 0, 0)
    return tuple(int(x) for x in match.groups())


class ZyxelFirmwareUpdate(ZyxelEntity, UpdateEntity):
    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Firmware"
    _attr_title = "Zyxel NWA firmware"
    _attr_should_poll = True
    # Use a normal Home Assistant entity icon for firmware updates.
    # The Zyxel brand artwork remains for the integration/device only.
    _attr_icon = "mdi:update"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.data.serial}_firmware_update"
        self._latest_version: str | None = None
        self._release_date: str | None = None
        self._last_check: datetime | None = None
        self._check_error: str | None = None

    @property
    def installed_version(self) -> str:
        return self.coordinator.data.firmware

    @property
    def latest_version(self) -> str | None:
        return self._latest_version

    @property
    def release_url(self) -> str:
        return DOWNLOAD_URL

    @property
    def release_summary(self) -> str | None:
        if self._latest_version and self._release_date:
            return f"Zyxel NWA210AX firmware {self._latest_version}, released {self._release_date}."
        return None

    @property
    def extra_state_attributes(self):
        return {
            "release_date": self._release_date,
            "last_checked": self._last_check.isoformat() if self._last_check else None,
            "check_error": self._check_error,
            "download_page": DOWNLOAD_URL,
        }

    def version_is_newer(self, latest_version: str, installed_version: str) -> bool:
        """Compare Zyxel firmware while treating the download-library C0 suffix as packaging."""
        return _normalise(latest_version) > _normalise(installed_version)

    async def async_update(self) -> None:
        now = datetime.now(timezone.utc)
        if self._last_check and now - self._last_check < CHECK_INTERVAL:
            return
        self._last_check = now
        try:
            session = async_get_clientsession(self.hass)
            async with session.get(DOWNLOAD_URL, timeout=15) as response:
                response.raise_for_status()
                html = await response.text()

            # Locate the first firmware version for NWA210AX. Zyxel currently renders
            # the download library server-side; tolerate whitespace/markup between fields.
            matches = re.findall(
                r"(?:Firmware.{0,1500}?)(\d+\.\d+\([A-Z0-9]+\.\d+\)C\d+)",
                html,
                flags=re.I | re.S,
            )
            if not matches:
                # Fallback if the page layout changes but version strings remain present.
                matches = re.findall(r"\d+\.\d+\([A-Z0-9]+\.\d+\)C\d+", html, flags=re.I)
            if not matches:
                raise ValueError("No firmware version found on Zyxel download page")

            versions = sorted(set(matches), key=_normalise, reverse=True)
            self._latest_version = versions[0]

            # Best-effort release date extraction near the selected version.
            pos = html.find(self._latest_version)
            nearby = html[pos:pos + 1200] if pos >= 0 else ""
            date_match = re.search(
                r"((?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4})",
                nearby,
                flags=re.I,
            )
            self._release_date = date_match.group(1) if date_match else None
            self._check_error = None
        except Exception as err:  # Keep the local integration healthy if Zyxel's site is unavailable.
            self._check_error = str(err)


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_add_entities([ZyxelFirmwareUpdate(entry.runtime_data)], update_before_add=True)
