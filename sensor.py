"""Sensor platform for Battery Manager."""
from __future__ import annotations

import logging
from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import BatteryManagerCoordinator

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Battery Manager sensors."""
    coordinator: BatteryManagerCoordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities([
        BatteryManagerBatterySocSensor(coordinator),
        BatteryManagerCurrentActionSensor(coordinator),
        BatteryManagerCurrentPriceSensor(coordinator),
        BatteryManagerChartSensor(coordinator),
    ])


class BatteryManagerBaseSensor(CoordinatorEntity, SensorEntity):
    """Base sensor for Battery Manager."""

    # Explicitly opt out of energy dashboard / statistics
    _attr_state_class = None
    _attr_device_class = None

    def __init__(self, coordinator: BatteryManagerCoordinator, key: str, name: str, icon: str) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_name = name
        self._attr_icon = icon
        self._attr_unique_id = f"battery_manager_{key}"

    @property
    def data(self):
        return self.coordinator.data or {}


class BatteryManagerBatterySocSensor(BatteryManagerBaseSensor):
    """Current battery SOC sensor."""

    def __init__(self, coordinator):
        super().__init__(coordinator, "battery_soc", "Battery Manager Battery SOC", "mdi:battery")
        self._attr_native_unit_of_measurement = "%"

    @property
    def native_value(self):
        return self.data.get("battery_soc")


class BatteryManagerCurrentActionSensor(BatteryManagerBaseSensor):
    """Current scheduled action sensor."""

    def __init__(self, coordinator):
        super().__init__(coordinator, "current_action", "Battery Manager Current Action", "mdi:battery")

    @property
    def icon(self) -> str:
        action = self.native_value
        if action == "charge":
            return "mdi:battery-charging"
        if action == "discharge":
            return "mdi:battery-arrow-down-outline"
        return "mdi:battery"

    @property
    def native_value(self):
        schedule = self.data.get("schedule", [])
        if not schedule:
            return "unknown"
        # Zoek het meest recente kwartier dat al gestart is
        from datetime import datetime
        now_dt = datetime.now()
        best_action = "unknown"
        best_dt = None
        for slot in schedule:
            starts_at = slot.get("starts_at", "")
            if not starts_at:
                continue
            try:
                dt = datetime.fromisoformat(
                    starts_at if "T" in starts_at else starts_at.replace(" ", "T")
                )
                dt_local = dt.astimezone(tz=None).replace(tzinfo=None)
            except (ValueError, TypeError):
                continue
            if dt_local <= now_dt:
                if best_dt is None or dt_local > best_dt:
                    best_dt = dt_local
                    best_action = slot.get("action", "normal")
        return best_action


class BatteryManagerCurrentPriceSensor(BatteryManagerBaseSensor):
    """Current electricity price sensor."""

    def __init__(self, coordinator):
        super().__init__(coordinator, "current_price", "Battery Manager Current Price", "mdi:currency-eur")
        self._attr_native_unit_of_measurement = "€/kWh"

    @property
    def native_value(self):
        schedule = self.data.get("schedule", [])
        if schedule:
            return schedule[0].get("price")
        return None


class BatteryManagerChartSensor(BatteryManagerBaseSensor):
    """Sensor that exposes full 48h chart data as attributes for the dashboard card."""

    # Voorkom dat de grote chart_data in de recorder/database wordt opgeslagen
    _unrecorded_attributes = frozenset({"chart_data", "last_updated"})

    def __init__(self, coordinator):
        super().__init__(coordinator, "chart", "Battery Manager Chart", "mdi:chart-line")

    @property
    def native_value(self):
        """Return number of quarters in the chart as the sensor state."""
        return len(self.data.get("chart_data", []))

    @property
    def extra_state_attributes(self):
        lu = getattr(self.coordinator, "last_update_success_time", None)
        return {
            "chart_data": self.data.get("chart_data", []),
            "last_updated": lu.isoformat() if lu else None,
        }
