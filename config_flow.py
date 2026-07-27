"""Config flow for Battery Manager."""
from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    DOMAIN,
    CONF_TIBBER_TOKEN, CONF_BATTERY_SOC,
    CONF_P1_METER,
    CONF_BATTERY_MODE, CONF_BATTERY_CHARGE_LIMIT, CONF_BATTERY_DISCHARGE_LIMIT,
    CONF_MANAGE_BATTERY,
    CONF_DEBUG_LOGGING, CONF_MIN_SOC,
    DEFAULT_BATTERY_CAPACITY, DEFAULT_BATTERY_MAX_CHARGE, DEFAULT_BATTERY_MAX_DISCHARGE,
    DEFAULT_BATTERY_COST, DEFAULT_BATTERY_CYCLES, DEFAULT_CHARGE_EFFICIENCY,
    DEFAULT_MIN_SOC, DEFAULT_MAX_SOC,
)

STEP_SENSORS_SCHEMA = vol.Schema({
    vol.Required(CONF_TIBBER_TOKEN): selector.selector({"text": {"type": "password"}}),
    vol.Required(CONF_BATTERY_SOC, default="sensor.solarflow_2400_ac_electric_level"): selector.selector({"entity": {"domain": "sensor"}}),
    vol.Required(CONF_P1_METER, default="sensor.p1_meter_actueel_watts"): selector.selector({"entity": {"domain": "sensor"}}),
})

STEP_DEVICES_SCHEMA = vol.Schema({
    vol.Required(CONF_BATTERY_MODE, default="select.solarflow_2400_ac_ac_mode"): selector.selector({"entity": {"domain": "select"}}),
    vol.Required(CONF_BATTERY_CHARGE_LIMIT, default="number.solarflow_2400_ac_input_limit"): selector.selector({"entity": {"domain": "number"}}),
    vol.Required(CONF_BATTERY_DISCHARGE_LIMIT, default="number.solarflow_2400_ac_output_limit"): selector.selector({"entity": {"domain": "number"}}),
})

STEP_CONTROL_SCHEMA = vol.Schema({
    vol.Required(CONF_MANAGE_BATTERY, default=True): selector.selector({"boolean": {}}),
})

class BatteryManagerOptionsFlow(config_entries.OptionsFlow):
    """Options flow — toggle debug logging without reconfiguring."""

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current_debug   = self.config_entry.options.get(CONF_DEBUG_LOGGING, False)
        current_min_soc = self.config_entry.options.get(CONF_MIN_SOC, DEFAULT_MIN_SOC)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required(CONF_DEBUG_LOGGING, default=current_debug): selector.selector({"boolean": {}}),
                vol.Required(CONF_MIN_SOC, default=current_min_soc): selector.selector({
                    "number": {
                        "min": 5,
                        "max": 30,
                        "step": 1,
                        "unit_of_measurement": "%",
                        "mode": "slider",
                    }
                }),
            }),
        )


class BatteryManagerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Battery Manager."""

    @staticmethod
    def async_get_options_flow(config_entry):
        return BatteryManagerOptionsFlow(config_entry)

    VERSION = 1

    def __init__(self):
        self._data = {}

    async def async_step_user(self, user_input=None):
        """Step 1: Tibber token + sensors."""
        errors = {}
        if user_input is not None:
            # Validate Tibber token
            token = user_input[CONF_TIBBER_TOKEN].strip()
            try:
                session = async_get_clientsession(self.hass)
                resp = await session.post(
                    "https://api.tibber.com/v1-beta/gql",
                    json={"query": "{ viewer { homes { id } } }"},
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                    timeout=10,
                )
                data = await resp.json()
                if "errors" in data or not data.get("data", {}).get("viewer"):
                    errors[CONF_TIBBER_TOKEN] = "invalid_token"
                else:
                    user_input[CONF_TIBBER_TOKEN] = token
                    self._data.update(user_input)
                    return await self.async_step_devices()
            except Exception:
                errors[CONF_TIBBER_TOKEN] = "cannot_connect"

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_SENSORS_SCHEMA,
            errors=errors,
            description_placeholders={"title": "Configure sensors"},
        )

    async def async_step_devices(self, user_input=None):
        """Step 2: Devices."""
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_control()

        return self.async_show_form(
            step_id="devices",
            data_schema=STEP_DEVICES_SCHEMA,
            description_placeholders={"title": "Configure devices"},
        )

    async def async_step_control(self, user_input=None):
        """Step 3: Control settings."""
        if user_input is not None:
            self._data.update(user_input)
            return self.async_create_entry(
                title="Battery Manager",
                data=self._data,
            )

        return self.async_show_form(
            step_id="control",
            data_schema=STEP_CONTROL_SCHEMA,
            description_placeholders={"title": "Control settings"},
        )
