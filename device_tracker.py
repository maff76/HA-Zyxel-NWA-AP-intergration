from __future__ import annotations
from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

async def async_setup_entry(hass,entry,async_add_entities):
    c=entry.runtime_data; entities={}
    def sync():
        new=[]
        for mac in c.known_clients:
            if mac not in entities: entities[mac]=ZyxelClientTracker(c,mac); new.append(entities[mac])
        if new: async_add_entities(new)
    sync(); entry.async_on_unload(c.async_add_listener(sync))

class ZyxelClientTracker(CoordinatorEntity,TrackerEntity):
    def __init__(self,c,mac): super().__init__(c); self.mac=mac; self._attr_unique_id=f"{c.data.serial}_client_{mac.replace(':','').lower()}"; self._attr_name=f"Wi-Fi client {mac}"
    @property
    def source_type(self): return SourceType.ROUTER
    @property
    def is_connected(self): return self.mac in self.coordinator.data.clients
    @property
    def extra_state_attributes(self):
        c=self.coordinator.data.clients.get(self.mac)
        if not c:return {"mac":self.mac}
        return {"mac":c.mac,"ip":c.ip,"ssid":c.ssid,"band":c.band,"radio":c.radio,"rssi_dbm":c.rssi,"tx_phy_rate_mbps":c.tx_rate_mbps,"rx_phy_rate_mbps":c.rx_rate_mbps,"rssi_percent":c.rssi_percent,"capability":c.capability,"dot11_features":c.dot11_features,"security":c.security,"connected_since":c.connected_since,"access_point":self.coordinator.data.model}
