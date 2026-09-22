from __future__ import annotations
import asyncio, re, time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.storage import Store
from .cgi import ZyxelCgiClient, ZyxelCgiError

@dataclass
class ClientData:
    mac:str; radio:int; band:str; ip:str=""; ssid:str=""; rssi:int|None=None; tx_rate_mbps:float|None=None; rx_rate_mbps:float|None=None; capability:str=""; security:str=""
    rssi_percent:int|None=None; connected_since:str=""; dot11_features:str=""
@dataclass
class RadioData:
    index:int; band:str; channel:int|None=None; clients:int=0; rx_octets:int|None=None; tx_octets:int|None=None; rx_bps:float|None=None; tx_bps:float|None=None
    channel_width_mhz:int|None=None; tx_power_dbm:int|None=None; channel_utilization:int|None=None
    rx_packets:int|None=None; tx_packets:int|None=None; retries:int|None=None; fcs_errors:int|None=None
@dataclass
class ZyxelData:
    model:str="NWA"; serial:str=""; firmware:str=""; ip:str=""; uptime_seconds:int=0; link:str=""; total_clients:int=0
    cpu_percent:float|None=None; cpu_cores:list[int]=field(default_factory=list); memory_percent:float|None=None
    memory_total_mb:float|None=None; memory_used_mb:float|None=None; memory_available_mb:float|None=None
    radios:dict[int,RadioData]=field(default_factory=dict); clients:dict[str,ClientData]=field(default_factory=dict)
    ethernet_rx_octets:int|None=None; ethernet_tx_octets:int|None=None; ethernet_rx_bps:float|None=None; ethernet_tx_bps:float|None=None; ethernet_speed_mbps:int|None=None

def _uptime_seconds(v:str)->int:
    m=re.search(r"(?:(\d+)\s+days?\s+)?(\d+):(\d+):(\d+)",v or "")
    return (int(m.group(1) or 0)*86400+int(m.group(2))*3600+int(m.group(3))*60+int(m.group(4))) if m else 0

def _link_speed(link:str)->int|None:
    m=re.search(r"(\d+)\s*[MG]",link or "",re.I)
    if not m:return None
    n=int(m.group(1)); return n*1000 if "G" in (link or "").upper() and n<100 else n

class ZyxelNwaCoordinator(DataUpdateCoordinator[ZyxelData]):
    def __init__(self,hass:HomeAssistant,host:str,cgi_client:ZyxelCgiClient,scan_interval:int=30):
        super().__init__(hass,logger=__import__('logging').getLogger(__name__),name="Zyxel NWA CGI",update_interval=timedelta(seconds=scan_interval))
        self.host=host; self.cgi_client=cgi_client; self._traffic_task=None; self._traffic_interval=2.0; self._traffic_listeners:set[Any]=set()
        self.traffic_poll_count=0; self.traffic_success_count=0; self.traffic_failure_count=0; self.traffic_last_duration_ms=None; self.traffic_last_interval_seconds=None; self._traffic_last_sample_monotonic=None
        self.traffic_last_error=None; self.traffic_last_success=None; self.traffic_last_failure=None; self.traffic_previous_raw={}; self.traffic_raw={}; self.traffic_deltas={}; self.traffic_elapsed_seconds=None
        self.traffic_rates_bps={"ethernet_rx":None,"ethernet_tx":None,"5ghz_rx":None,"5ghz_tx":None,"2_4ghz_rx":None,"2_4ghz_tx":None}
        self.known_clients:set[str]=set(); self.last_success=None; self.last_failure=None; self.last_failure_message=None; self.consecutive_failures=0
        # Persistent lifetime traffic totals. The AP's own counters reset on reboot,
        # so accumulate deltas in Home Assistant and periodically persist them.
        self.traffic_totals={k:0 for k in self.traffic_rates_bps}
        self._traffic_totals_initialized=False
        self._traffic_store=Store(hass,1,"zyxel_nwa_traffic_totals")
        self._traffic_store_dirty=False
        self._traffic_last_store_monotonic=0.0

    async def async_load_traffic_totals(self):
        saved=await self._traffic_store.async_load()
        if isinstance(saved,dict):
            totals=saved.get("totals",{})
            if isinstance(totals,dict):
                for k in self.traffic_totals:
                    try: self.traffic_totals[k]=max(0,int(totals.get(k,0)))
                    except (TypeError,ValueError): pass
            self._traffic_totals_initialized=bool(saved.get("initialized",False))

    async def _async_save_traffic_totals(self,force=False):
        now=time.monotonic()
        if not self._traffic_store_dirty and not force:return
        if not force and now-self._traffic_last_store_monotonic<30:return
        await self._traffic_store.async_save({"initialized":self._traffic_totals_initialized,"totals":self.traffic_totals})
        self._traffic_store_dirty=False; self._traffic_last_store_monotonic=now

    async def async_reset_traffic_totals(self):
        self.traffic_totals={k:0 for k in self.traffic_totals}
        self._traffic_totals_initialized=True
        self._traffic_store_dirty=True
        await self._async_save_traffic_totals(force=True)
        self._push_traffic()

    def async_add_traffic_listener(self,cb):
        self._traffic_listeners.add(cb)
        return lambda:self._traffic_listeners.discard(cb)
    def _push_traffic(self):
        for cb in tuple(self._traffic_listeners): cb()
    def async_start_traffic_polling(self,entry:ConfigEntry):
        if self._traffic_task is None or self._traffic_task.done(): self._traffic_task=entry.async_create_background_task(self.hass,self._traffic_poll_loop(),"zyxel_nwa_cgi_traffic_poll")
    async def async_stop_traffic_polling(self):
        if self._traffic_task:
            self._traffic_task.cancel()
            try: await self._traffic_task
            except asyncio.CancelledError: pass
            self._traffic_task=None

    async def _traffic_poll_loop(self):
        prev_radio=None; prev_time=None
        while True:
            started=time.monotonic(); self.traffic_poll_count+=1
            try:
                s=await self.cgi_client.async_get_traffic(); now=time.monotonic(); self.traffic_success_count+=1; self.traffic_last_success=datetime.now(timezone.utc); self.traffic_last_error=None
                self.traffic_last_duration_ms=(now-started)*1000
                if self._traffic_last_sample_monotonic is not None:self.traffic_last_interval_seconds=now-self._traffic_last_sample_monotonic
                self._traffic_last_sample_monotonic=now
                cur={"ethernet_rx":s.ethernet_rx_bytes,"ethernet_tx":s.ethernet_tx_bytes,"5ghz_rx":s.radio_5_rx_bytes,"5ghz_tx":s.radio_5_tx_bytes,"2_4ghz_rx":s.radio_24_rx_bytes,"2_4ghz_tx":s.radio_24_tx_bytes}
                self.traffic_previous_raw=dict(self.traffic_raw); self.traffic_raw=cur
                # Lifetime totals survive AP reboots. On the first ever sample,
                # seed them from the AP's current since-boot counters. Thereafter
                # add only deltas. If a raw counter decreases, treat it as an AP
                # counter reset and add the new post-reset value.
                if not self._traffic_totals_initialized:
                    self.traffic_totals={k:max(0,int(v or 0)) for k,v in cur.items()}
                    self._traffic_totals_initialized=True; self._traffic_store_dirty=True
                elif self.traffic_previous_raw:
                    for k,v in cur.items():
                        old=self.traffic_previous_raw.get(k)
                        if old is None: continue
                        delta_total=(v-old) if v>=old else v
                        if delta_total>0:
                            self.traffic_totals[k]+=int(delta_total); self._traffic_store_dirty=True
                await self._async_save_traffic_totals()
                rates=dict(self.traffic_rates_bps); rates["ethernet_rx"]=s.ethernet_rx_bps_bytes*8.0; rates["ethernet_tx"]=s.ethernet_tx_bps_bytes*8.0
                deltas={k:None for k in cur}; radio={k:cur[k] for k in ("5ghz_rx","5ghz_tx","2_4ghz_rx","2_4ghz_tx")}
                if prev_radio is not None and prev_time is not None and now>prev_time:
                    elapsed=now-prev_time; self.traffic_elapsed_seconds=elapsed
                    for k,v in radio.items():
                        old=prev_radio[k]; delta=v-old if v>=old else None; deltas[k]=delta; rates[k]=(delta*8.0/elapsed) if delta is not None else None
                if self.traffic_previous_raw:
                    for k in ("ethernet_rx","ethernet_tx"):
                        old=self.traffic_previous_raw.get(k); deltas[k]=cur[k]-old if old is not None and cur[k]>=old else None
                prev_radio=radio; prev_time=now; self.traffic_deltas=deltas; self.traffic_rates_bps=rates; self._push_traffic()
            except (ZyxelCgiError,asyncio.TimeoutError) as e:
                self.traffic_failure_count+=1; self.traffic_last_failure=datetime.now(timezone.utc); self.traffic_last_error=str(e)
            await asyncio.sleep(max(0.0,self._traffic_interval-(time.monotonic()-started)))

    async def _async_update_data(self)->ZyxelData:
        try:
            s=await self.cgi_client.async_get_system()
            d=ZyxelData(model=s.model,serial=s.serial,firmware=s.firmware,ip=s.ip,uptime_seconds=_uptime_seconds(s.uptime),total_clients=s.radio_24_clients+s.radio_5_clients,cpu_percent=s.cpu_percent,memory_percent=s.memory_percent)
            d.radios={
                1:RadioData(1,"2.4 GHz",s.radio_24_channel,s.radio_24_clients,channel_width_mhz=s.radio_24_width_mhz,tx_power_dbm=s.radio_24_tx_power_dbm,channel_utilization=s.radio_24_utilization,rx_packets=s.radio_24_rx_packets,tx_packets=s.radio_24_tx_packets,retries=s.radio_24_retries,fcs_errors=s.radio_24_fcs_errors),
                2:RadioData(2,"5 GHz",s.radio_5_channel,s.radio_5_clients,channel_width_mhz=s.radio_5_width_mhz,tx_power_dbm=s.radio_5_tx_power_dbm,channel_utilization=s.radio_5_utilization,rx_packets=s.radio_5_rx_packets,tx_packets=s.radio_5_tx_packets,retries=s.radio_5_retries,fcs_errors=s.radio_5_fcs_errors),
            }
            d.clients={mac:ClientData(mac,c.slot,c.band,c.ip,c.ssid,c.rssi,c.tx_rate_mbps,c.rx_rate_mbps,c.capability,c.security,c.rssi_percent,c.connected_since,c.dot11_features) for mac,c in s.clients.items()}
            self.known_clients.update(d.clients)
            # Merge the fast CGI traffic snapshot without waiting for the next 30 s poll.
            if self.traffic_raw:
                d.ethernet_rx_octets=self.traffic_raw.get("ethernet_rx"); d.ethernet_tx_octets=self.traffic_raw.get("ethernet_tx")
                for ridx,p in ((1,"2_4ghz"),(2,"5ghz")):
                    d.radios[ridx].rx_octets=self.traffic_raw.get(p+"_rx"); d.radios[ridx].tx_octets=self.traffic_raw.get(p+"_tx")
            # Network monitor traffic poll contains port link status; use a lightweight port query here too.
            p=await self.cgi_client.async_get_port_status(); d.link=p[0]; d.ethernet_speed_mbps=_link_speed(d.link)
            self.consecutive_failures=0; self.last_success=datetime.now(timezone.utc); self.last_failure_message=None
            return d
        except (ZyxelCgiError,asyncio.TimeoutError) as e:
            self.consecutive_failures+=1; self.last_failure=datetime.now(timezone.utc); self.last_failure_message=str(e); raise UpdateFailed(f"Zyxel CGI poll failed: {e}") from e
