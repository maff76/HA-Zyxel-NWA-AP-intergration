from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN
class ZyxelEntity(CoordinatorEntity):
    @property
    def device_info(self):
        d=self.coordinator.data
        return DeviceInfo(identifiers={(DOMAIN,d.serial or d.model)},name=d.model,manufacturer="Zyxel",model=d.model,serial_number=d.serial,sw_version=d.firmware,configuration_url=f"http://{self.coordinator.host}")
