from __future__ import annotations
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_USERNAME, CONF_PASSWORD
from homeassistant.data_entry_flow import FlowResult
from .const import DOMAIN
from .cgi import ZyxelCgiClient, ZyxelCgiError

class ZyxelNwaConfigFlow(config_entries.ConfigFlow,domain=DOMAIN):
    VERSION=2
    async def async_step_user(self,user_input=None)->FlowResult:
        errors={}
        if user_input is not None:
            client=ZyxelCgiClient(self.hass,user_input[CONF_HOST],user_input[CONF_USERNAME],user_input[CONF_PASSWORD])
            try:
                info=await client.async_get_system()
                await self.async_set_unique_id(info.serial or user_input[CONF_HOST]); self._abort_if_unique_id_configured()
                return self.async_create_entry(title=f"{info.model} ({user_input[CONF_HOST]})",data=user_input)
            except (ZyxelCgiError,TimeoutError): errors["base"]="cannot_connect"
            finally: await client.async_close()
        return self.async_show_form(step_id="user",data_schema=vol.Schema({vol.Required(CONF_HOST):str,vol.Required(CONF_USERNAME,default="admin"):str,vol.Required(CONF_PASSWORD):str}),errors=errors)
    @staticmethod
    def async_get_options_flow(config_entry): return ZyxelOptionsFlow(config_entry)

class ZyxelOptionsFlow(config_entries.OptionsFlow):
    def __init__(self,entry): self.entry=entry
    async def async_step_init(self,user_input=None):
        if user_input is not None:return self.async_create_entry(title="",data=user_input)
        return self.async_show_form(step_id="init",data_schema=vol.Schema({vol.Required(CONF_USERNAME,default=self.entry.options.get(CONF_USERNAME,self.entry.data.get(CONF_USERNAME,"admin"))):str,vol.Required(CONF_PASSWORD,default=self.entry.options.get(CONF_PASSWORD,self.entry.data.get(CONF_PASSWORD,""))):str}))
