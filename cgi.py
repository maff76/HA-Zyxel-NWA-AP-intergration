"""Authenticated client for the NWA standalone web UI CGI."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import unescape
from urllib.parse import urljoin

from aiohttp import ClientError, ClientTimeout, CookieJar
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession

class ZyxelCgiError(Exception): pass
class ZyxelCgiAuthError(ZyxelCgiError): pass

@dataclass(frozen=True)
class CgiTraffic:
    ethernet_rx_bps_bytes:int; ethernet_tx_bps_bytes:int
    ethernet_rx_bytes:int; ethernet_tx_bytes:int
    radio_24_rx_bytes:int; radio_24_tx_bytes:int
    radio_5_rx_bytes:int; radio_5_tx_bytes:int

@dataclass(frozen=True)
class CgiClient:
    mac:str; ip:str=""; slot:int=0; band:str=""; ssid:str=""; rssi:int|None=None
    tx_rate_mbps:float|None=None; rx_rate_mbps:float|None=None; capability:str=""; security:str=""
    rssi_percent:int|None=None; connected_since:str=""; dot11_features:str=""

@dataclass(frozen=True)
class CgiSystem:
    model:str=""; firmware:str=""; serial:str=""; uptime:str=""; ip:str=""
    cpu_percent:float|None=None; memory_percent:float|None=None
    radio_24_channel:int|None=None; radio_5_channel:int|None=None
    radio_24_clients:int=0; radio_5_clients:int=0
    radio_24_width_mhz:int|None=None; radio_5_width_mhz:int|None=None
    radio_24_tx_power_dbm:int|None=None; radio_5_tx_power_dbm:int|None=None
    radio_24_utilization:int|None=None; radio_5_utilization:int|None=None
    radio_24_rx_packets:int|None=None; radio_5_rx_packets:int|None=None
    radio_24_tx_packets:int|None=None; radio_5_tx_packets:int|None=None
    radio_24_retries:int|None=None; radio_5_retries:int|None=None
    radio_24_fcs_errors:int|None=None; radio_5_fcs_errors:int|None=None
    clients:dict[str,CgiClient]=field(default_factory=dict)

def _field(block,name):
    m=re.search(rf"'{re.escape(name)}'\s*:\s*'([^']*)'",block)
    if not m: raise ZyxelCgiError(f"Missing CGI field {name}")
    return unescape(m.group(1))
def _int_field(block,name): return int(re.search(r"-?\d+",_field(block,name)).group())
def _rate(v):
    m=re.search(r"([0-9.]+)\s*([KMG]?)",v,re.I)
    if not m:return None
    return float(m.group(1))*{"":1,"K":.001,"M":1,"G":1000}[m.group(2).upper()]
def _slot_block(text,slot):
    m=re.search(rf"\{{'__name':'{slot}'(?P<body>.*?)\}}",text,re.S)
    if not m: raise ZyxelCgiError(f"Missing wireless slot {slot}")
    return m.group(0)
def _command_result(text,index,label):
    errno=re.search(rf"var errno{index}=([^;]+);",text); errmsg=re.search(rf"var errmsg{index}='([^']*)';",text)
    if not errno:
        preview=" ".join(text[:240].split())
        if "<!DOCTYPE" in text or "<html" in text.lower(): raise ZyxelCgiAuthError(f"{label}: web session is not authenticated")
        raise ZyxelCgiError(f"{label}: missing errno{index}; response={preview!r}")
    if errno.group(1).strip()!="0": raise ZyxelCgiError(f"{label}: errno={errno.group(1).strip()}, errmsg={errmsg.group(1) if errmsg else 'unknown'}")

def _zyshdata(text,index):
    m=re.search(rf"var zyshdata{index}=(.*?);\s*var errno{index}=",text,re.S)
    if not m: raise ZyxelCgiError(f"Missing zyshdata{index}")
    return m.group(1)

class ZyxelCgiClient:
    def __init__(self,hass:HomeAssistant,host:str,username:str|None=None,password:str|None=None):
        self._session=async_create_clientsession(hass, cookie_jar=CookieJar(unsafe=True)); self._base=f"http://{host}/"; self._url=urljoin(self._base,"cgi-bin/zysh-cgi")
        self._username=username or ""; self._password=password or ""; self._authenticated=False
        self._browser_ua="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36"
        self._login_get_headers={
            "Accept":"text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Accept-Language":"en-GB,en;q=0.9,en-US;q=0.8",
            "Cache-Control":"max-age=0",
            "DNT":"1",
            "Upgrade-Insecure-Requests":"1",
            "User-Agent":self._browser_ua,
        }
        self._login_post_headers={
            **self._login_get_headers,
            "Origin":self._base.rstrip('/'),
            "Referer":self._base,
            "Content-Type":"application/x-www-form-urlencoded",
        }
        self._headers={"Accept":"*/*","X-Requested-With":"XMLHttpRequest","Origin":self._base.rstrip('/'),"Referer":urljoin(self._base,"ext-js/web-pages/index/index.html"),"User-Agent":self._browser_ua}

    @property
    def has_credentials(self): return bool(self._username and self._password)

    async def async_close(self):
        """Close the private CGI session and its IP-safe cookie jar."""
        if not self._session.closed:
            await self._session.close()

    async def async_login(self):
        if not self.has_credentials: raise ZyxelCgiAuthError("Web UI username/password are not configured")
        try:
            async with self._session.get(self._base,headers=self._login_get_headers,timeout=ClientTimeout(total=5)) as r:
                page=await r.text()
            patterns=[
                r'name=["\']CSRFToken["\'][^>]*value\s*=\s*(?:["\']([^"\']+)["\']|([^\s>]+))',
                r'value\s*=\s*(?:["\']([^"\']+)["\']|([^\s>]+))[^>]*name=["\']CSRFToken["\']',
            ]
            token = None
            for pattern in patterns:
                if match := re.search(pattern, page, re.I):
                    token = next((group for group in match.groups() if group), None)
                    if token:
                        break
            if not token: raise ZyxelCgiAuthError("Login page did not contain CSRFToken")
            data={"fake_safari_username":"","fake_safari_password":"","username":self._username,"pwd":self._password,"password":self._password,"CSRFToken":unescape(token)}
            async with self._session.post(self._base,data=data,headers=self._login_post_headers,allow_redirects=False,timeout=ClientTimeout(total=5)) as r:
                body=await r.text()
                if r.status not in (301,302,303):
                    alert = re.search(r"<div[^>]+id=[\"']alert_div[\"'][^>]*>(.*?)</div>", body, re.I | re.S)
                    alert_text = re.sub(r"<[^>]+>", " ", alert.group(1)).strip() if alert else ""
                    detail = f"; alert={alert_text!r}" if alert_text else ""
                    raise ZyxelCgiAuthError(f"Login failed: HTTP {r.status}{detail}; response={' '.join(body[:160].split())!r}")
            cookies = self._session.cookie_jar.filter_cookies(self._base)
            if "authtok" not in cookies:
                raise ZyxelCgiAuthError(
                    "Login redirected but Zyxel authtok cookie was not retained"
                )
            self._authenticated=True
        except (ClientError,TimeoutError) as e: raise ZyxelCgiAuthError(str(e)) from e

    async def _post(self,data,label,retry_auth=True):
        if not self._authenticated: await self.async_login()
        try:
            async with self._session.post(self._url,data=data,headers=self._headers,timeout=ClientTimeout(total=4)) as r:
                text=await r.text()
                if r.status!=200: raise ZyxelCgiError(f"{label}: HTTP {r.status}")
        except (ClientError,TimeoutError) as e: raise ZyxelCgiError(f"{label}: {e}") from e
        if "<!DOCTYPE" in text or "<html" in text.lower():
            self._authenticated=False
            if retry_auth:
                await self.async_login(); return await self._post(data,label,False)
            raise ZyxelCgiAuthError(f"{label}: authentication expired")
        return text

    async def _commands(self,commands,label):
        data=[("filter","js2")]+[("cmd",c) for c in commands]+[("write","0")]
        text=await self._post(data,label)
        for i,c in enumerate(commands): _command_result(text,i,c)
        return text

    async def async_get_traffic(self):
        port=await self._commands(["show port status","show system uptime","show interface _all ap","show version","show ipv6 interface lan","show port setting"],"Network Monitor")
        wireless=await self._commands(["show wlan all","show wlan radio macaddr","show wlan-radio-profile all","show wireless-hal statistic","show wireless-hal station number","show wireless-hal current channel","show load-balancing loading","show country-code match_status","show mac"],"Wireless Monitor")
        pm=re.search(r"\{'_Port':'1'(?P<body>.*?)\}",port,re.S)
        if not pm: raise ZyxelCgiError("UPLINK port statistics not found")
        p=pm.group(0); s1=_slot_block(wireless,1); s2=_slot_block(wireless,2)
        return CgiTraffic(_int_field(p,"_RxB_s"),_int_field(p,"_TxB_s"),_int_field(p,"_RxBytes"),_int_field(p,"_TxBytes"),_int_field(s1,"_wlanReceivedByte"),_int_field(s1,"_wlanTransmittedByte"),_int_field(s2,"_wlanReceivedByte"),_int_field(s2,"_wlanTransmittedByte"))

    async def async_get_system(self):
        cmds=["show version","show serial-number","show cpu status","show mem status","show system uptime","show interface summary all","show wireless-hal current channel","show wireless-hal station number","show wireless-hal station info","show wireless-hal statistic"]
        t=await self._commands(cmds,"CGI system poll")
        version=_zyshdata(t,0); serial=_zyshdata(t,1); cpu=_zyshdata(t,2); mem=_zyshdata(t,3); uptime=_zyshdata(t,4); iface=_zyshdata(t,5); channels=_zyshdata(t,6); counts=_zyshdata(t,7); stations=_zyshdata(t,8); stats=_zyshdata(t,9)
        ipm=re.search(r"'_Name':'lan'.*?'_IP_Address':'([^']+)'",iface,re.S)
        clients={}
        for b in re.findall(r"\{'__name':'\d+'.*?\}",stations,re.S):
            try:
                mac=_field(b,"_MAC").upper(); slot=_int_field(b,"_Slot")
                clients[mac]=CgiClient(
                    mac,_field(b,"_IPv4"),slot,_field(b,"_Band"),_field(b,"_SSID"),
                    _int_field(b,"_RSSI_dBm"),_rate(_field(b,"_TxRate")),_rate(_field(b,"_RxRate")),
                    _field(b,"_Capability"),_field(b,"_Security"),_int_field(b,"_RSSI"),
                    _field(b,"_Time"),_field(b,"_DOT11_features")
                )
            except (ZyxelCgiError,ValueError): continue
        st1=_slot_block(stats,1); st2=_slot_block(stats,2)
        ch24=_field(channels,"_Slot1"); ch5=_field(channels,"_Slot2")
        def width(v):
            m=re.search(r"\((\d+)\s*MHz\)",v,re.I)
            return int(m.group(1)) if m else None
        return CgiSystem(
            model=_field(version,"_model"),firmware=_field(version,"_firmware_version"),serial=_field(serial,"_serial_number"),uptime=_field(uptime,"_system_uptime"),ip=ipm.group(1) if ipm else "",
            cpu_percent=float(_int_field(cpu,"_CPU_utilization")),memory_percent=float(_int_field(mem,"_memory_usage")),
            radio_24_channel=int(re.search(r"\d+",ch24).group()),radio_5_channel=int(re.search(r"\d+",ch5).group()),
            radio_24_clients=_int_field(counts,"_Slot1"),radio_5_clients=_int_field(counts,"_Slot2"),
            radio_24_width_mhz=width(ch24),radio_5_width_mhz=width(ch5),
            radio_24_tx_power_dbm=_int_field(st1,"_TxPower"),radio_5_tx_power_dbm=_int_field(st2,"_TxPower"),
            radio_24_utilization=_int_field(st1,"_Channel_Utilization"),radio_5_utilization=_int_field(st2,"_Channel_Utilization"),
            radio_24_rx_packets=_int_field(st1,"_ReceivedPktCount"),radio_5_rx_packets=_int_field(st2,"_ReceivedPktCount"),
            radio_24_tx_packets=_int_field(st1,"_TransmittedPktCount"),radio_5_tx_packets=_int_field(st2,"_TransmittedPktCount"),
            radio_24_retries=_int_field(st1,"_RetryCount"),radio_5_retries=_int_field(st2,"_RetryCount"),
            radio_24_fcs_errors=_int_field(st1,"_FCSErrorCount"),radio_5_fcs_errors=_int_field(st2,"_FCSErrorCount"),clients=clients)
    async def async_get_port_status(self):
        """Return (link status, live RX B/s, live TX B/s) for the uplink."""
        text=await self._commands(["show port status"],"CGI port status")
        data=_zyshdata(text,0)
        pm=re.search(r"\{'_Port':'1'(?P<body>.*?)\}",data,re.S)
        if not pm: raise ZyxelCgiError("UPLINK port status not found")
        p=pm.group(0)
        return (_field(p,"_Status"),_int_field(p,"_RxB_s"),_int_field(p,"_TxB_s"))

