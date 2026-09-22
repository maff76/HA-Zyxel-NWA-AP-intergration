from __future__ import annotations

from dataclasses import dataclass
from typing import Callable,Any
from homeassistant.components.sensor import SensorEntity,SensorDeviceClass,SensorStateClass
from homeassistant.const import PERCENTAGE,UnitOfDataRate,UnitOfInformation
from homeassistant.helpers.entity import EntityCategory
from .entity import ZyxelEntity

@dataclass(frozen=True)
class Desc:
    key:str; name:str; value:Callable[[Any],Any]; unit:str|None=None; icon:str|None=None; device_class:SensorDeviceClass|None=None; state_class:SensorStateClass|None=None; category:EntityCategory|None=None

DESCS=[
 Desc("firmware","Firmware",lambda d:d.firmware,category=EntityCategory.DIAGNOSTIC),
 Desc("uptime","Uptime",lambda d:format_uptime(d.uptime_seconds),icon="mdi:timer-outline",category=EntityCategory.DIAGNOSTIC),
 Desc("link","Ethernet link",lambda d:d.link,icon="mdi:ethernet",category=EntityCategory.DIAGNOSTIC),
 Desc("clients","Connected clients",lambda d:d.total_clients,icon="mdi:wifi-marker"),
 Desc("cpu","CPU usage",lambda d:d.cpu_percent,PERCENTAGE,"mdi:cpu-64-bit",state_class=SensorStateClass.MEASUREMENT),
 Desc("memory","Memory usage",lambda d:d.memory_percent,PERCENTAGE,"mdi:memory",state_class=SensorStateClass.MEASUREMENT),
]

async def async_setup_entry(hass,entry,async_add_entities):
    c=entry.runtime_data
    ents=[ZyxelSensor(c,x) for x in DESCS]
    ents.append(CgiStatusSensor(c))
    ents += [EthernetTrafficSensor(c,"download_rate"), EthernetTrafficSensor(c,"upload_rate"),
             EthernetTrafficSensor(c,"download_total"), EthernetTrafficSensor(c,"upload_total"),
             EthernetLinkSpeedSensor(c)]
    for ridx in (2,1):
        ents += [RadioSensor(c,ridx,"channel"),RadioSensor(c,ridx,"clients"),
                 RadioDetailSensor(c,ridx,"channel_width"),RadioDetailSensor(c,ridx,"channel_utilization"),
                 RadioDetailSensor(c,ridx,"tx_power"),RadioDetailSensor(c,ridx,"rx_packets"),
                 RadioDetailSensor(c,ridx,"tx_packets"),RadioDetailSensor(c,ridx,"retries"),
                 RadioDetailSensor(c,ridx,"fcs_errors"),
                 RadioTrafficSensor(c,ridx,"rx_rate"), RadioTrafficSensor(c,ridx,"tx_rate"),
                 RadioTrafficSensor(c,ridx,"rx_total"), RadioTrafficSensor(c,ridx,"tx_total")]
    async_add_entities(ents)

def format_uptime(seconds: int) -> str:
    seconds = max(0, int(seconds or 0))
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{days}d {hours}h {minutes}m {seconds}s"

class ZyxelSensor(ZyxelEntity,SensorEntity):
    def __init__(self,c,d): super().__init__(c); self.d=d; self._attr_unique_id=f"{c.data.serial}_{d.key}"; self._attr_name=d.name; self._attr_native_unit_of_measurement=d.unit; self._attr_icon=d.icon; self._attr_device_class=d.device_class; self._attr_state_class=d.state_class; self._attr_entity_category=d.category
    @property
    def native_value(self): return self.d.value(self.coordinator.data)
    @property
    def extra_state_attributes(self): return ({"cores":self.coordinator.data.cpu_cores} if self.d.key=="cpu" else ({"uptime_seconds":self.coordinator.data.uptime_seconds} if self.d.key=="uptime" else None))





class TrafficUpdateMixin:
    """Subscribe an entity to the coordinator's dedicated traffic update signal."""

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.async_add_traffic_listener(self._handle_traffic_update)
        )

    def _handle_traffic_update(self) -> None:
        # The callback runs in Home Assistant's event loop.  Force a state write so
        # the freshly calculated two-second rate is visible immediately.
        self.async_write_ha_state()


class CgiStatusSensor(ZyxelEntity,SensorEntity):
    """Diagnostic view of the authenticated Zyxel CGI connection."""
    def __init__(self,c):
        super().__init__(c); self._attr_name="CGI status"
        self._attr_unique_id=f"{c.data.serial}_cgi_status"
        self._attr_icon="mdi:web"; self._attr_entity_category=EntityCategory.DIAGNOSTIC
    @property
    def available(self): return True
    @property
    def native_value(self): return "Connected" if self.coordinator.last_update_success else "Retrying"
    @property
    def extra_state_attributes(self):
        c=self.coordinator
        return {
            "api":"Zyxel authenticated web CGI",
            "system_poll_interval_seconds":c.update_interval.total_seconds(),
            "last_successful_system_poll":c.last_success.isoformat() if c.last_success else None,
            "last_system_failure":c.last_failure.isoformat() if c.last_failure else "None",
            "last_system_failure_message":c.last_failure_message or "None",
            "traffic_poll_interval_seconds":c._traffic_interval,
            "traffic_poll_count":c.traffic_poll_count,
            "traffic_success_count":c.traffic_success_count,
            "traffic_failure_count":c.traffic_failure_count,
            "traffic_last_duration_ms":round(c.traffic_last_duration_ms,1) if c.traffic_last_duration_ms is not None else None,
            "traffic_actual_interval_seconds":round(c.traffic_last_interval_seconds,3) if c.traffic_last_interval_seconds is not None else None,
            "traffic_last_error":c.traffic_last_error or "None",
        }


class EthernetTrafficSensor(TrafficUpdateMixin,ZyxelEntity,SensorEntity):
    def __init__(self,c,key):
        super().__init__(c); self.key=key
        names={"download_rate":"Ethernet download rate","upload_rate":"Ethernet upload rate",
               "download_total":"Ethernet data received","upload_total":"Ethernet data sent"}
        self._attr_name=names[key]
        self._attr_unique_id=f"{c.data.serial}_ethernet_{key}"
        if key.endswith("_rate"):
            self._attr_native_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND
            self._attr_state_class=SensorStateClass.MEASUREMENT
            self._attr_icon="mdi:download-network" if key.startswith("download") else "mdi:upload-network"
        else:
            # Keep these as plain numeric GB counters.  Do not use DATA_SIZE here:
            # Home Assistant may preserve a per-entity display-unit override (for
            # example B) from an earlier version and convert the GB value back to
            # a huge byte number in the UI.  A literal GB unit makes the entity
            # consistently display the human-readable value we publish.
            self._attr_native_unit_of_measurement="GB"
            self._attr_state_class=SensorStateClass.TOTAL_INCREASING
            self._attr_icon="mdi:database-arrow-down" if key.startswith("download") else "mdi:database-arrow-up"
    @property
    def native_value(self):
        c=self.coordinator
        if self.key=="download_rate":
            v=c.traffic_rates_bps.get("ethernet_rx")
            return round(v/1_000_000,3) if v is not None else None
        if self.key=="upload_rate":
            v=c.traffic_rates_bps.get("ethernet_tx")
            return round(v/1_000_000,3) if v is not None else None
        if self.key=="download_total":
            v=c.traffic_totals.get("ethernet_rx")
            return round(v/1_000_000_000,3) if v is not None else None
        v=c.traffic_totals.get("ethernet_tx")
        return round(v/1_000_000_000,3) if v is not None else None

class EthernetLinkSpeedSensor(ZyxelEntity,SensorEntity):
    def __init__(self,c):
        super().__init__(c)
        self._attr_name="Ethernet link speed"
        self._attr_unique_id=f"{c.data.serial}_ethernet_link_speed"
        self._attr_native_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND
        self._attr_state_class=SensorStateClass.MEASUREMENT
        self._attr_icon="mdi:ethernet"
        self._attr_entity_category=EntityCategory.DIAGNOSTIC
    @property
    def native_value(self): return self.coordinator.data.ethernet_speed_mbps
    @property
    def extra_state_attributes(self): return {"link":self.coordinator.data.link}

class RadioSensor(ZyxelEntity,SensorEntity):
    def __init__(self,c,ridx,key): super().__init__(c); self.ridx=ridx; self.key=key; band=c.data.radios[ridx].band; prefix="5 GHz" if ridx==2 else "2.4 GHz"; self._attr_name=f"{prefix} {key}"; self._attr_unique_id=f"{c.data.serial}_radio{ridx}_{key}"; self._attr_icon="mdi:wifi"
    @property
    def native_value(self):
        r=self.coordinator.data.radios[self.ridx]; return r.channel if self.key=="channel" else r.clients


class RadioDetailSensor(ZyxelEntity,SensorEntity):
    """Additional per-radio statistics exposed by wireless-hal."""
    def __init__(self,c,ridx,key):
        super().__init__(c); self.ridx=ridx; self.key=key
        prefix="5 GHz" if ridx==2 else "2.4 GHz"
        names={"channel_width":"Channel width","channel_utilization":"Channel utilization","tx_power":"TX power","rx_packets":"Packets received","tx_packets":"Packets transmitted","retries":"Retries","fcs_errors":"FCS errors"}
        self._attr_name=f"{prefix} {names[key]}"
        self._attr_unique_id=f"{c.data.serial}_radio{ridx}_{key}"
        self._attr_entity_category=EntityCategory.DIAGNOSTIC
        if key=="channel_width": self._attr_native_unit_of_measurement="MHz"; self._attr_icon="mdi:arrow-expand-horizontal"
        elif key=="channel_utilization": self._attr_native_unit_of_measurement=PERCENTAGE; self._attr_state_class=SensorStateClass.MEASUREMENT; self._attr_icon="mdi:gauge"
        elif key=="tx_power": self._attr_native_unit_of_measurement="dBm"; self._attr_state_class=SensorStateClass.MEASUREMENT; self._attr_icon="mdi:signal"
        else: self._attr_state_class=SensorStateClass.TOTAL_INCREASING; self._attr_icon="mdi:counter"
    @property
    def native_value(self):
        r=self.coordinator.data.radios[self.ridx]
        return {"channel_width":r.channel_width_mhz,"channel_utilization":r.channel_utilization,"tx_power":r.tx_power_dbm,"rx_packets":r.rx_packets,"tx_packets":r.tx_packets,"retries":r.retries,"fcs_errors":r.fcs_errors}[self.key]

class RadioTrafficSensor(TrafficUpdateMixin,ZyxelEntity,SensorEntity):
    def __init__(self,c,ridx,key):
        super().__init__(c); self.ridx=ridx; self.key=key; band=c.data.radios[ridx].band
        prefix="5 GHz" if ridx==2 else "2.4 GHz"
        # Present traffic from the connected client's point of view.
        names={"rx_rate":"Upload rate","tx_rate":"Download rate","rx_total":"Data uploaded","tx_total":"Data downloaded"}
        self._attr_name=f"{prefix} {names[key]}"
        self._attr_unique_id=f"{c.data.serial}_radio{ridx}_{key}"
        if key.endswith("_rate"):
            self._attr_native_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND
            self._attr_state_class=SensorStateClass.MEASUREMENT
            self._attr_icon="mdi:upload-network" if key.startswith("rx") else "mdi:download-network"
        else:
            # Keep these as plain numeric GB counters.  Do not use DATA_SIZE here:
            # Home Assistant may preserve a per-entity display-unit override (for
            # example B) from an earlier version and convert the GB value back to
            # a huge byte number in the UI.  A literal GB unit makes the entity
            # consistently display the human-readable value we publish.
            self._attr_native_unit_of_measurement="GB"
            self._attr_state_class=SensorStateClass.TOTAL_INCREASING
            self._attr_icon="mdi:database-arrow-up" if key.startswith("rx") else "mdi:database-arrow-down"
    @property
    def native_value(self):
        c=self.coordinator
        prefix="5ghz" if self.ridx==2 else "2_4ghz"
        if self.key=="rx_rate":
            v=c.traffic_rates_bps.get(f"{prefix}_rx")
            return round(v/1_000_000,3) if v is not None else None
        if self.key=="tx_rate":
            v=c.traffic_rates_bps.get(f"{prefix}_tx")
            return round(v/1_000_000,3) if v is not None else None
        if self.key=="rx_total":
            v=c.traffic_totals.get(f"{prefix}_rx")
            return round(v/1_000_000_000,3) if v is not None else None
        v=c.traffic_totals.get(f"{prefix}_tx")
        return round(v/1_000_000_000,3) if v is not None else None
