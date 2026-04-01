"""Battery Manager integration for Home Assistant."""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from .coordinator import BatteryManagerCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor"]

_JS_FILENAME = "battery-dashboard.js"
_RESOURCE_URL_BASE = "/local/battery_manager/battery-dashboard.js"


def _file_hash(path: Path) -> str:
    """Return first 8 chars of the MD5 hash of a file."""
    h = hashlib.md5(path.read_bytes()).hexdigest()
    return h[:8]


def _deploy_frontend(hass: HomeAssistant) -> None:
    """Copy the frontend JS and update the Lovelace resource URL with a cache-busting hash."""
    src = Path(__file__).parent / "frontend" / _JS_FILENAME
    www_dir = Path(hass.config.config_dir) / "www" / "battery_manager"
    dst = www_dir / _JS_FILENAME

    try:
        www_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        _LOGGER.info("Battery Manager: frontend gekopieerd naar %s", dst)
    except Exception as exc:
        _LOGGER.warning("Battery Manager: kon frontend niet kopieren: %s", exc)
        return

    try:
        new_hash = _file_hash(dst)
        new_url = f"{_RESOURCE_URL_BASE}?v={new_hash}"
        storage_path = Path(hass.config.config_dir) / ".storage" / "lovelace_resources"
        if not storage_path.exists():
            # Maak een leeg resources-bestand aan zodat de resource wordt toegevoegd
            data = {"version": 1, "minor_version": 1, "key": "lovelace_resources", "data": {"items": []}}
            _LOGGER.info("Battery Manager: lovelace_resources storage aangemaakt")
        else:
            data = json.loads(storage_path.read_text())
        changed = False
        # Herkent zowel de nieuwe URL-basis als de oude energy_manager URL
        _OLD_URL_PATTERNS = [
            _RESOURCE_URL_BASE,
            "/local/energy_manager/energy-dashboard",
        ]
        items = data.setdefault("data", {}).setdefault("items", [])
        for item in items:
            url = item.get("url", "")
            if any(pat in url for pat in _OLD_URL_PATTERNS) and url != new_url:
                _LOGGER.info("Battery Manager: resource URL bijgewerkt: %s -> %s", url, new_url)
                item["url"] = new_url
                changed = True
        if not changed:
            # Resource bestaat nog niet — voeg hem toe
            items.append({"id": str(uuid.uuid4()), "url": new_url, "type": "module"})
            _LOGGER.info("Battery Manager: Lovelace resource aangemaakt in storage: %s", new_url)
            changed = True
        if changed:
            storage_path.write_text(json.dumps(data, indent=2))
    except Exception as exc:
        _LOGGER.warning("Battery Manager: kon resource URL niet bijwerken: %s", exc)


async def _async_update_lovelace_resource(hass: HomeAssistant, new_url: str) -> None:
    """Update the Lovelace resource URL in HA's in-memory collection (persists on save)."""
    try:
        lovelace_data = hass.data.get("lovelace", {})
        resources = lovelace_data.get("resources")
        if resources is None:
            _LOGGER.debug("Battery Manager: Lovelace resources collection niet gevonden in hass.data")
            return
        items = await resources.async_items()
        _OLD_URL_PATTERNS = [_RESOURCE_URL_BASE, "/local/energy_manager/energy-dashboard"]
        found = False
        for item in items:
            url = item.get("url", "")
            if any(pat in url for pat in _OLD_URL_PATTERNS):
                found = True
                if url != new_url:
                    await resources.async_update_item(
                        item["id"],
                        {"url": new_url, "type": item.get("type", "module"), "id": item["id"]},
                    )
                    _LOGGER.info(
                        "Battery Manager: Lovelace resource URL in-memory bijgewerkt: %s → %s",
                        url, new_url,
                    )
        if not found:
            await resources.async_create_item({"url": new_url, "type": "module"})
            _LOGGER.info("Battery Manager: Lovelace resource in-memory aangemaakt: %s", new_url)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.debug("Battery Manager: kon in-memory Lovelace resource niet bijwerken: %s", exc)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Battery Manager component."""

    hass.data.setdefault(DOMAIN, {})

    # Stap 1: kopieer JS naar www (altijd, ook op herstart)
    await hass.async_add_executor_job(_deploy_frontend, hass)
    # Fallback: forceer toevoegen resource als hij ontbreekt
    try:
        from homeassistant.components.lovelace.resources import async_add_external_resource
        www_dir = Path(hass.config.config_dir) / "www" / "battery_manager"
        dst = www_dir / _JS_FILENAME
        new_hash = await hass.async_add_executor_job(_file_hash, dst)
        new_url = f"{_RESOURCE_URL_BASE}?v={new_hash}"
        _LOGGER.warning("Battery Manager: fallback resource check — probeer toe te voegen: %s", new_url)
        await async_add_external_resource(hass, new_url, "module")
        _LOGGER.warning("Battery Manager: fallback resource toegevoegd via API: %s", new_url)
    except Exception as exc:
        _LOGGER.warning("Battery Manager: fallback resource toevoegen via API mislukt: %s", exc)

    # Stap 1b: voeg de resource toe via de officiële Home Assistant API (vanaf 2023.4+)
    try:
        from homeassistant.components.lovelace.resources import async_add_external_resource
        www_dir = Path(hass.config.config_dir) / "www" / "battery_manager"
        dst = www_dir / _JS_FILENAME
        new_hash = await hass.async_add_executor_job(_file_hash, dst)
        new_url = f"{_RESOURCE_URL_BASE}?v={new_hash}"
        await async_add_external_resource(hass, new_url, "module")
        _LOGGER.info("Battery Manager: Lovelace resource automatisch toegevoegd via API: %s", new_url)
    except Exception as exc:
        _LOGGER.warning("Battery Manager: kon Lovelace resource niet automatisch toevoegen via API: %s", exc)

    # Stap 2: update de in-memory Lovelace resource URL NADAT HA volledig is opgestart.
    # Zo overschrijft HA onze aanpassing niet (Lovelace schrijft in-memory state
    # terug naar disk bij elke save; door na startup te updaten staat de hash in
    # de gecachede collectie en wordt hij meegenomen bij de volgende save).
    async def _post_start_deploy(event=None):
        src = Path(__file__).parent / "frontend" / _JS_FILENAME
        try:
            new_hash = await hass.async_add_executor_job(_file_hash, src)
        except Exception:
            return
        new_url = f"{_RESOURCE_URL_BASE}?v={new_hash}"
        await _async_update_lovelace_resource(hass, new_url)

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _post_start_deploy)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Battery Manager from a config entry."""
    if not isinstance(hass.data.get(DOMAIN), dict):
        hass.data[DOMAIN] = {}

    coordinator = BatteryManagerCoordinator(hass, entry)

    # Luister naar SOC-sensor wijzigingen en cache de waarde direct.
    # Dit vangt het geval op waarbij SolarFlow trager opstart dan Battery Manager.
    soc_entity = entry.data.get("battery_soc", "")
    if soc_entity:
        async def _on_soc_change(event):
            new_state = event.data.get("new_state")
            if new_state is None or new_state.state in ("unavailable", "unknown", "none", ""):
                return
            try:
                live = float(new_state.state)
                if live > 0:
                    was_stale = coordinator._last_known_soc != live
                    coordinator._last_known_soc = live
                    await hass.async_add_executor_job(coordinator._save_soc_cache, live)
                    _LOGGER.debug(
                        "battery_manager: SOC-cache bijgewerkt via listener: %.1f%%", live
                    )
                    # Als de waarde veranderd is t.o.v. wat de coordinator gebruikte,
                    # forceer een directe herberekening zodat de grafiek meteen klopt.
                    if was_stale:
                        hass.async_create_task(coordinator.async_refresh())
            except (ValueError, TypeError):
                pass

        entry.async_on_unload(
            async_track_state_change_event(hass, [soc_entity], _on_soc_change)
        )

    # Luister naar wanneer de Zendure mode-entiteit van unavailable → beschikbaar gaat.
    # Dit vangt het geval op waarbij Zendure later opstart dan Battery Manager:
    # de eerste update sloeg het actie-commando over (entities unavailable), maar
    # zodra de entiteit beschikbaar wordt triggeren we direct een herberekening.
    mode_entity = entry.data.get("battery_mode", "")
    if mode_entity:
        _mode_was_unavailable: list[bool] = [False]

        async def _on_mode_available(event):
            old_state = event.data.get("old_state")
            new_state = event.data.get("new_state")
            old_unavailable = old_state is None or old_state.state in ("unavailable", "unknown")
            new_available = new_state is not None and new_state.state not in ("unavailable", "unknown")
            if old_unavailable and new_available:
                _LOGGER.info(
                    "battery_manager: Zendure mode-entiteit beschikbaar gekomen (%s) — herberekening gestart",
                    new_state.state,
                )
                hass.async_create_task(coordinator.async_refresh())

        entry.async_on_unload(
            async_track_state_change_event(hass, [mode_entity], _on_mode_available)
        )

    # Start eerste refresh als achtergrondtaak (blokkeert HA-start niet).
    hass.async_create_task(coordinator.async_refresh())

    # Plan een tweede refresh op T+60s: dan zijn SolarFlow e.a. integraties
    # vrijwel zeker online en heeft de SOC-sensor een geldige waarde.
    async def _delayed_refresh(_now=None):
        _LOGGER.debug("battery_manager: T+60s herstart-refresh")
        await coordinator.async_refresh()

    from homeassistant.helpers.event import async_call_later
    entry.async_on_unload(async_call_later(hass, 60, _delayed_refresh))
    hass.data[DOMAIN][entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # ── Kwartiergrens-timer: herbereken actie exact op :00, :15, :30, :45 ──
    # De coordinator draait elke 15 min, maar niet gesynchroniseerd met de
    # kwartiergrens. Deze timer zorgt dat de actie direct wisselt op het
    # juiste moment.
    from homeassistant.helpers.event import async_track_utc_time_change

    async def _on_quarter_boundary(now):
        """Re-evaluate battery action at each quarter boundary."""
        if coordinator.data and coordinator.data.get("schedule"):
            _LOGGER.info("battery_manager: kwartiergrens %s — herberekening actie", now.strftime("%H:%M"))
            await coordinator._apply_battery_control(coordinator.data["schedule"])

    entry.async_on_unload(
        async_track_utc_time_change(hass, _on_quarter_boundary, minute=[0, 15, 30, 45], second=5)
    )

    # Fallback: controleer na 2 minuten of de chart-sensor bestaat, anders forceer een refresh en log een waarschuwing
    async def _fallback_check_sensor(_now=None):
        entity_id = "sensor.battery_manager_chart"
        state = hass.states.get(entity_id)
        if state is None:
            _LOGGER.warning("Battery Manager: Fallback — sensor '%s' bestaat niet na 2 minuten, forceer refresh", entity_id)
            await coordinator.async_refresh()
        else:
            _LOGGER.info("Battery Manager: Fallback — sensor '%s' bestaat en heeft attribuut chart_data: %s", entity_id, "chart_data" in (state.attributes or {}))

    entry.async_on_unload(async_call_later(hass, 120, _fallback_check_sensor))

    # Service: battery_manager.force_action
    # Test-hulpmiddel: stuur de batterij direct aan zonder te wachten op het schema.
    # Gebruik via Developer Tools → Services:
    #   service: battery_manager.force_action
    #   data:
    #     action: charge      # of: discharge / normal / all_on
    async def _handle_force_action(call):
        action = call.data.get("action", "normal")
        _LOGGER.warning("battery_manager: force_action aangeroepen met actie='%s'", action)
        now_iso = datetime.now().replace(second=0, microsecond=0).isoformat()
        synthetic_schedule = [{
            "quarter": 0,
            "starts_at": now_iso,
            "action": action,
            "price": 0.0,
            "battery_soc": None,
        }]
        coordinator._last_applied_action = None  # forceer altijd uitvoering
        await coordinator._apply_battery_control(synthetic_schedule)

    hass.services.async_register(DOMAIN, "force_action", _handle_force_action)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
