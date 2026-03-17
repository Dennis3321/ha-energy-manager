from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DOMAIN,
    CONF_TIBBER_TOKEN, CONF_BATTERY_SOC,
    CONF_P1_METER, CONF_VACATION,
    CONF_BATTERY_MODE, CONF_BATTERY_CHARGE_LIMIT, CONF_BATTERY_DISCHARGE_LIMIT,
    CONF_MANAGE_BATTERY,
    DEFAULT_BATTERY_CAPACITY, DEFAULT_BATTERY_MAX_CHARGE, DEFAULT_BATTERY_MAX_DISCHARGE,
    DEFAULT_CHARGE_EFFICIENCY, DEFAULT_MIN_SOC, DEFAULT_MAX_SOC,
    DEFAULT_BATTERY_COST, DEFAULT_BATTERY_CYCLES,
    CONF_DEBUG_LOGGING, CONF_MIN_SOC,
)

_LOGGER = logging.getLogger(__name__)

# Module-level laadbevestiging — wordt geschreven bij elke (herstart)
try:
    Path("/root/config/bm_loaded.txt").write_text(
        f"coordinator geladen om {__import__('datetime').datetime.now()}"
    )
except Exception:
    pass

TIBBER_API_URL = "https://api.tibber.com/v1-beta/gql"
TIBBER_PRICE_QUERY = """{
  viewer {
    homes {
      currentSubscription {
        priceInfo {
          today { startsAt total energy tax }
          tomorrow { startsAt total energy tax }
        }
      }
    }
  }
}"""

BATTERY_DEPRECIATION = DEFAULT_BATTERY_COST / (DEFAULT_BATTERY_CYCLES * DEFAULT_BATTERY_CAPACITY)

# Days of week in Dutch for nice chart labels
_WEEKDAYS_NL = ["ma", "di", "wo", "do", "vr", "za", "zo"]

class BatteryManagerCoordinator(DataUpdateCoordinator):
        def _get_dynamic_limits(self):
            """Lees de actuele laad- en ontlaadlimieten uit de entiteiten (indien beschikbaar)."""
            config = self.entry.data
            charge_entity = config.get(CONF_BATTERY_CHARGE_LIMIT, "")
            discharge_entity = config.get(CONF_BATTERY_DISCHARGE_LIMIT, "")
            charge_limit = DEFAULT_BATTERY_MAX_CHARGE
            discharge_limit = DEFAULT_BATTERY_MAX_DISCHARGE
            # Probeer actuele waardes te lezen
            if charge_entity:
                state = self.hass.states.get(charge_entity)
                if state and state.state not in ("unavailable", "unknown", "none", ""):
                    try:
                        charge_limit = float(state.state)
                    except (ValueError, TypeError):
                        pass
            if discharge_entity:
                state = self.hass.states.get(discharge_entity)
                if state and state.state not in ("unavailable", "unknown", "none", ""):
                    try:
                        discharge_limit = float(state.state)
                    except (ValueError, TypeError):
                        pass
            return charge_limit, discharge_limit
    """Coordinator to manage battery data updates."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=15),
        )
        self.entry = entry
        self._last_applied_action: str | None = None  # voorkomt herhaalde identieke commando's
        self._pending_diag: str | None = None  # diag-inhoud klaar voor executor-write
        self._soc_cache_path = (
            Path(hass.config.config_dir) / ".storage" / "battery_manager_soc_cache.json"
        )
        # Cache wordt geladen bij de eerste async update (niet hier — blocking I/O verboden in __init__)
        self._last_known_soc: float | None = None
        self._soc_cache_loaded: bool = False

    def _dbg(self, msg: str, *args) -> None:
        """Log at INFO level when debug_logging option is enabled."""
        if self.entry.options.get(CONF_DEBUG_LOGGING, False):
            _LOGGER.warning("[EM-DEBUG] " + msg, *args)

    async def _async_update_data(self):
        """Fetch and process all energy data."""
        try:
            config = self.entry.data

            # Fetch prices directly from Tibber API
            prices_today, prices_tomorrow = await self._fetch_tibber_prices(
                config[CONF_TIBBER_TOKEN]
            )
            self._dbg("Tibber: %d prijzen vandaag, %d morgen",
                      len(prices_today), len(prices_tomorrow))

            # Laad SOC-cache bij eerste update (mag niet in __init__ — blocking I/O)
            if not self._soc_cache_loaded:
                self._last_known_soc = await self.hass.async_add_executor_job(self._load_soc_cache)
                self._soc_cache_loaded = True
                # Overschrijf cache met live sensorwaarde als die al beschikbaar is
                soc_entity = config.get(CONF_BATTERY_SOC, "")
                if soc_entity:
                    state = self.hass.states.get(soc_entity)
                    if state and state.state not in ("unavailable", "unknown", "none", ""):
                        try:
                            live = float(state.state)
                            if live > 0:
                                self._last_known_soc = live
                                await self.hass.async_add_executor_job(self._save_soc_cache, live)
                                _LOGGER.info("battery_manager: SOC direct bij eerste update gelezen: %.1f%%", live)
                        except (ValueError, TypeError):
                            pass

            battery_soc    = self._read_battery_soc(config[CONF_BATTERY_SOC])
            # SOC-cache schrijven buiten de event loop
            if self._last_known_soc is not None:
                await self.hass.async_add_executor_job(
                    self._save_soc_cache, self._last_known_soc
                )
            p1             = self._safe_float(self._get_state(config[CONF_P1_METER]))
            vacation       = self._get_state(config[CONF_VACATION]) == "on"
            self._dbg("Sensoren: battery_soc=%.1f%%, p1=%.0f W, vacation=%s",
                      battery_soc, p1, vacation)

            all_prices = prices_today + prices_tomorrow

            # If Tibber has no data yet, generate synthetic 48h quarters so the
            # chart shows usage estimates even without price data.
            if not all_prices:
                _LOGGER.warning(
                    "battery_manager: Tibber API returned no prices — "
                    "generating synthetic 48h quarters (price=None)."
                )
                now = datetime.now().replace(minute=0, second=0, microsecond=0)
                for i in range(96):  # 96 quarters = 48 h
                    slot = now + timedelta(minutes=15 * i)
                    all_prices.append({
                        "startsAt": slot.isoformat(),
                        "total": None,
                    })
            else:
                self._dbg("all_prices: %d kwartieren (eerste: %s %.4f, laatste: %s %.4f)",
                          len(all_prices),
                          all_prices[0].get("startsAt", "?")[:16], all_prices[0].get("total") or 0,
                          all_prices[-1].get("startsAt", "?")[:16], all_prices[-1].get("total") or 0)

            min_soc = self.entry.options.get(CONF_MIN_SOC, DEFAULT_MIN_SOC)

            schedule = self._create_schedule(
                all_prices, battery_soc, p1, vacation, min_soc
            )

            # Diagnosefile schrijven buiten event loop
            if getattr(self, '_pending_diag', None):
                diag_path = Path(self.hass.config.config_dir) / "battery_manager_diag.txt"
                await self.hass.async_add_executor_job(
                    diag_path.write_text, self._pending_diag
                )
                self._pending_diag = None

            chart_data = self._build_chart_data(
                all_prices, schedule, battery_soc
            )

            # Stuur de batterij aan op basis van het huidige kwartier in het schema
            await self._apply_battery_control(schedule)

            self._dbg("Schema: %d kwartieren, acties: %s",
                      len(schedule),
                      {
                          a: sum(1 for s in schedule if s["action"] == a)
                          for a in {s["action"] for s in schedule}
                      })
            self._dbg("Chart data: %d punten aangemaakt", len(chart_data))

            return {
                "prices_today": prices_today,
                "prices_tomorrow": prices_tomorrow,
                "all_prices": all_prices,
                "battery_soc": battery_soc,
                "p1": p1,
                "vacation": vacation,
                "schedule": schedule,
                "chart_data": chart_data,
            }

        except Exception as err:
            raise UpdateFailed(f"Error updating energy data: {err}") from err

    def _build_chart_data(self, all_prices, schedule, battery_soc: float):
        """Build per-quarter chart data for the next 48 hours.

        De battery_soc in de chart is een projectie op basis van uitsluitend
        grid-acties (laden/ontladen uit het schema). Zonne-opbrengst speelt
        geen rol — bij hoge prijs is terugleveren waardevoller dan accumuladen.

        battery_soc = de ECHTE huidige SOC op dit moment; dit is het startpunt
        van de witte SOC-lijn op de 'nu'-grens.
        """
        action_by_idx = {s["quarter"]: s["action"] for s in schedule}
        # Verleden-kwartieren hebben battery_soc=None in het schema
        is_future_by_idx = {s["quarter"]: s["battery_soc"] is not None for s in schedule}
        projected_soc: float | None = None

        # Haal actuele limieten op voor projectie
        charge_limit, discharge_limit = self._get_dynamic_limits()
        chart = []
        for i, quarter in enumerate(all_prices):
            try:
                starts_at_raw = quarter.get("startsAt", "")
                if "T" in starts_at_raw:
                    dt = datetime.fromisoformat(starts_at_raw)
                else:
                    dt = datetime.fromisoformat(starts_at_raw.replace(" ", "T"))
                dt_local = dt.astimezone(tz=None).replace(tzinfo=None)
            except (ValueError, TypeError, AttributeError):
                dt_local = None

            if dt_local:
                show_day = (dt_local.minute == 0 and dt_local.hour == 0) or i == 0
                label = (
                    f"{_WEEKDAYS_NL[dt_local.weekday()]} {dt_local.strftime('%H:%M')}"
                    if show_day
                    else dt_local.strftime("%H:%M")
                )
            else:
                label = str(i)

            # usage_w verwijderd
            action_i = action_by_idx.get(i, "normal")
            is_future = is_future_by_idx.get(i, False)

            # Op het eerste toekomstige kwartier: initialiseer met de ECHTE huidige SOC
            if is_future and projected_soc is None:
                projected_soc = battery_soc

            # SOC-projectie puur op grid-acties (zon speelt geen rol — terugleveren
            # bij hoge prijs is waardevoller dan accumuladen met zonne-energie).
            chart_soc: float | None = None
            if projected_soc is not None and is_future:
                if action_i in ("charge", "all_on"):
                    energy_added = (charge_limit / 1000) * 0.25 * DEFAULT_CHARGE_EFFICIENCY
                    projected_soc = min(DEFAULT_MAX_SOC, projected_soc + (energy_added / DEFAULT_BATTERY_CAPACITY) * 100)
                elif action_i == "discharge":
                    energy_used = (discharge_limit / 1000) * 0.25 * DEFAULT_CHARGE_EFFICIENCY
                    projected_soc = max(0.0, projected_soc - (energy_used / DEFAULT_BATTERY_CAPACITY) * 100)
                else:
                    projected_soc = max(0.0, projected_soc - 0.05)
                chart_soc = round(projected_soc, 1)

            if action_i in ("charge", "all_on"):
                battery_flow_w = charge_limit
            elif action_i == "discharge":
                battery_flow_w = -discharge_limit
            else:
                battery_flow_w = 0

            # Besparing t.o.v. geen batterij: 0.6 kWh grid per kwartier bij volledige power
            # discharge = positief (we kopen minder van het net)
            # charge    = negatief (we kopen extra van het net)
            _kwh_grid = (charge_limit / 1000) * 0.25  # 0.6 kWh
            _price = quarter.get("total", 0) or 0
            if action_i == "discharge":
                savings_delta = round(_price * _kwh_grid, 4)
            elif action_i in ("charge", "all_on"):
                savings_delta = round(-_price * _kwh_grid, 4)
            else:
                savings_delta = 0.0

            chart.append({
                "time": label,
                "starts_at": starts_at_raw,
                "price": round(quarter.get("total", 0) or 0, 4),
                "action": action_i,
                "battery_soc": chart_soc,
                "battery_flow_w": battery_flow_w,
                "savings_delta": savings_delta,
            })

        return chart

    def _expand_to_quarters(self, hourly_prices: list) -> list:
        """Fallback: expand hourly slots into 4 × 15-minute quarter slots (flat price)."""
        quarters = []
        for slot in hourly_prices:
            starts_at_raw = slot.get("startsAt", "")
            try:
                dt = datetime.fromisoformat(
                    starts_at_raw if "T" in starts_at_raw
                    else starts_at_raw.replace(" ", "T")
                )
            except (ValueError, TypeError):
                dt = None

            for q in range(4):
                if dt is not None:
                    quarter_starts = (dt + timedelta(minutes=15 * q)).isoformat()
                else:
                    quarter_starts = starts_at_raw if q == 0 else ""
                quarters.append({**slot, "startsAt": quarter_starts})
        return quarters


    def _load_soc_cache(self) -> float | None:
        """Lees de gecachede SOC uit het opslagbestand (overleeft HA-herstart)."""
        try:
            data = json.loads(self._soc_cache_path.read_text())
            value = float(data.get("soc", 0))
            if value > 0:
                _LOGGER.info("battery_manager: SOC-cache geladen: %.1f%%", value)
                return value
        except (FileNotFoundError, KeyError, ValueError, TypeError):
            pass
        return None

    def _save_soc_cache(self, value: float) -> None:
        """Sla de huidige SOC op zodat de volgende HA-herstart ermee start."""
        try:
            self._soc_cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._soc_cache_path.write_text(json.dumps({"soc": round(value, 1)}))
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("battery_manager: SOC-cache opslaan mislukt: %s", exc)

    def _read_battery_soc(self, entity_id: str) -> float:
        """Lees de batterij-SOC; val terug op laatste bekende waarde bij unavailable/unknown."""
        raw = self._get_state(entity_id)
        try:
            value = float(raw)
            if value > 0:
                self._last_known_soc = value
                # Cache wordt via executor geschreven vanuit _async_update_data
                return value
        except (ValueError, TypeError):
            pass
        # Sensor geeft unavailable / unknown / 0 — gebruik gecachede SOC
        if self._last_known_soc is not None:
            _LOGGER.debug(
                "battery_manager: SOC sensor '%s' levert '%s' — "
                "gebruik laatste bekende SOC %.1f%%",
                entity_id, raw, self._last_known_soc,
            )
            return self._last_known_soc
        _LOGGER.warning(
            "battery_manager: SOC sensor '%s' levert '%s' en geen cache beschikbaar — "
            "gebruik 50%% als veilige standaard",
            entity_id, raw,
        )
        return 50.0

    def _get_state(self, entity_id: str):
        """Get state of an entity."""
        state = self.hass.states.get(entity_id)
        return state.state if state else None

    def _get_attribute(self, entity_id: str, attribute: str):
        """Get attribute of an entity."""
        state = self.hass.states.get(entity_id)
        return state.attributes.get(attribute) if state else None

    @staticmethod
    def _safe_float(value, default: float = 0.0) -> float:
        """Convert value to float, returning default for None/unavailable/unknown."""
        if value is None:
            return default
        try:
            return float(value)
        except (ValueError, TypeError):
            return default

    async def _fetch_tibber_prices(self, token: str) -> tuple[list, list]:
        """Fetch today and tomorrow hourly prices from Tibber GraphQL API."""
        session = async_get_clientsession(self.hass)
        try:
            self._dbg("Tibber API: verbinding maken met %s", TIBBER_API_URL)
            async with session.post(
                TIBBER_API_URL,
                json={"query": TIBBER_PRICE_QUERY},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                timeout=10,
            ) as resp:
                self._dbg("Tibber API: HTTP status %s", resp.status)
                data = await resp.json()

            homes = data["data"]["viewer"]["homes"]
            self._dbg("Tibber API: %d home(s) gevonden", len(homes))
            for home in homes:
                price_info = (
                    home.get("currentSubscription") or {}
                ).get("priceInfo") or {}
                today    = price_info.get("today")    or []
                tomorrow = price_info.get("tomorrow") or []
                self._dbg("Tibber API home: today=%d uur, tomorrow=%d uur",
                          len(today), len(tomorrow))
                if today:
                    # Tibber geeft uurprijzen; verdubbel elk uur naar 4 kwartieren
                    return self._expand_to_quarters(today), self._expand_to_quarters(tomorrow)
            _LOGGER.warning("battery_manager: Tibber API gaf geen 'today' prijzen terug — respons: %s",
                            str(data)[:300])
            return [], []
        except Exception as err:
            _LOGGER.warning("battery_manager: Tibber API error: %s", err)
            return [], []

    def _find_current_action(self, schedule: list) -> dict:
        """Zoek het kwartier dat nu actief is en geef het volledige slot terug."""
        now_dt = datetime.now()
        best_slot: dict = {"action": "normal", "starts_at": "", "price": None}
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
            # Meest recente kwartier dat al gestart is (dt_local <= now)
            if dt_local <= now_dt:
                if best_dt is None or dt_local > best_dt:
                    best_dt = dt_local
                    best_slot = slot
        return best_slot

    async def _apply_battery_control(self, schedule: list) -> None:
        """Stuur de Zendure-batterij aan op basis van het huidige kwartier.

        Entiteiten (geconfigureerd in de config-flow):
          select  CONF_BATTERY_MODE          AC Input Mode / AC Output Mode
          number  CONF_BATTERY_CHARGE_LIMIT  0-800 W
          number  CONF_BATTERY_DISCHARGE_LIMIT 0-800 W
        """
        config = self.entry.data
        if not config.get(CONF_MANAGE_BATTERY, False):
            _LOGGER.warning("battery_manager: batterijsturing uitgeschakeld (CONF_MANAGE_BATTERY=False)")
            return

        mode_entity      = config.get(CONF_BATTERY_MODE, "")
        charge_entity    = config.get(CONF_BATTERY_CHARGE_LIMIT, "")
        discharge_entity = config.get(CONF_BATTERY_DISCHARGE_LIMIT, "")
        if not mode_entity:
            _LOGGER.warning("battery_manager: geen mode-entiteit geconfigureerd, batterij niet aangestuurd")
            return

        current_slot = self._find_current_action(schedule)
        action = current_slot.get("action", "normal")
        slot_time = (current_slot.get("starts_at") or "")[11:16] or "onbekend"
        slot_price = current_slot.get("price")
        price_str = f"€{slot_price:.4f}/kWh" if slot_price is not None else "prijs onbekend"

        _LOGGER.warning(
            "battery_manager: actief kwartier %s (%s) → actie='%s'",
            slot_time, price_str, action,
        )

        # Hardwarematige veiligheidscheck: niet ontladen als live SOC ≤ min_soc
        # Dit staat VOOR de _last_applied_action check zodat het altijd evalueert.
        if action == "discharge":
            min_soc_guard = self.entry.options.get(CONF_MIN_SOC, DEFAULT_MIN_SOC)
            live_soc = self._read_battery_soc(config.get(CONF_BATTERY_SOC, ""))
            if live_soc <= min_soc_guard:
                _LOGGER.warning(
                    "battery_manager: ontladen GEBLOKKEERD op %s — live SOC %.1f%% ≤ min_soc %.1f%% → teruggevallen op 'normal'",
                    slot_time, live_soc, min_soc_guard,
                )
                action = "normal"
                # Reset zodat volgende cyclus opnieuw evalueert
                self._last_applied_action = None

        # Geen commando als de actie niet veranderd is t.o.v. vorige cyclus,
        # TENZIJ het laden/ontladen betreft — stuur dat altijd om te voorkomen
        # dat de Zendure door een interne timeout terugvalt naar stand-by.
        if action == self._last_applied_action and action not in ("charge", "all_on", "discharge"):
            _LOGGER.info(
                "battery_manager: actie '%s' onveranderd t.o.v. vorige cyclus — geen commando verstuurd",
                action,
            )
            return

        if self._last_applied_action and self._last_applied_action != action:
            _LOGGER.warning(
                "battery_manager: ACTIE GEWIJZIGD '%s' → '%s' op kwartier %s (%s)",
                self._last_applied_action, action, slot_time, price_str,
            )

        # Bepaal doelstatus per actie, met dynamische limieten
        dyn_charge_limit, dyn_discharge_limit = self._get_dynamic_limits()
        if action in ("charge", "all_on"):
            mode_option    = "input"
            charge_limit   = dyn_charge_limit
            discharge_limit = 0
        elif action == "discharge":
            mode_option    = "output"
            charge_limit   = 0
            discharge_limit = dyn_discharge_limit
        else:
            mode_option    = "input"
            charge_limit   = 0
            discharge_limit = 0

        _LOGGER.warning(
            "battery_manager: batterij actie '%s' -> mode=%s, laden=%dW, ontladen=%dW",
            action, mode_option, charge_limit, discharge_limit,
        )

        try:
            # Retry-lus: wacht tot entiteiten beschikbaar zijn (max 3 pogingen, 10s vertraging)
            _RETRY_ATTEMPTS = 3
            _RETRY_DELAY = 10
            for attempt in range(_RETRY_ATTEMPTS):
                mode_state     = self.hass.states.get(mode_entity)
                charge_state   = self.hass.states.get(charge_entity) if charge_entity else None
                discharge_state = self.hass.states.get(discharge_entity) if discharge_entity else None

                entities_ok = (
                    mode_state and mode_state.state not in ("unavailable", "unknown")
                    and (not charge_entity or (charge_state and charge_state.state not in ("unavailable", "unknown")))
                    and (not discharge_entity or (discharge_state and discharge_state.state not in ("unavailable", "unknown")))
                )
                if entities_ok:
                    break
                if attempt < _RETRY_ATTEMPTS - 1:
                    _LOGGER.debug(
                        "battery_manager: Zendure-entiteiten nog niet beschikbaar, poging %d/%d, wacht %ds",
                        attempt + 1, _RETRY_ATTEMPTS, _RETRY_DELAY,
                    )
                    await asyncio.sleep(_RETRY_DELAY)
            else:
                # Alle pogingen uitgeput
                unavailable = [
                    e for e, s in [
                        (mode_entity, mode_state),
                        (charge_entity, charge_state),
                        (discharge_entity, discharge_state),
                    ] if e and (not s or s.state in ("unavailable", "unknown"))
                ]
                _LOGGER.warning(
                    "battery_manager: Zendure-entiteiten blijven unavailable na %d pogingen, actie '%s' overgeslagen: %s",
                    _RETRY_ATTEMPTS, action, ", ".join(unavailable),
                )
                return

            # 1. Stel laad- en ontlaadlimiet in
            if charge_entity:
                _LOGGER.warning(
                    "battery_manager: stel laadlimiet in → %s = %d W", charge_entity, charge_limit
                )
                await self.hass.services.async_call(
                    "number", "set_value",
                    {"entity_id": charge_entity, "value": charge_limit},
                    blocking=True,
                )
            if discharge_entity:
                _LOGGER.warning(
                    "battery_manager: stel ontlaadlimiet in → %s = %d W", discharge_entity, discharge_limit
                )
                await self.hass.services.async_call(
                    "number", "set_value",
                    {"entity_id": discharge_entity, "value": discharge_limit},
                    blocking=True,
                )
            # 2. Schakel modus (na de limieten, zodat de Zendure ze al kent)
            _LOGGER.warning(
                "battery_manager: stel AC mode in → %s = '%s'", mode_entity, mode_option
            )
            await self.hass.services.async_call(
                "select", "select_option",
                {"entity_id": mode_entity, "option": mode_option},
                blocking=True,
            )
            _LOGGER.warning("battery_manager: batterij-aansturing geslaagd: actie='%s'", action)
            self._last_applied_action = action

            # 3. Verificatie: wacht kort zodat Zendure MQTT-terugkoppeling verwerkt,
            #    lees dan de werkelijke waarden terug en log afwijkingen.
            await asyncio.sleep(2)
            for _eid, _req, _label in [
                (charge_entity,    charge_limit,    "laadlimiet"),
                (discharge_entity, discharge_limit, "ontlaadlimiet"),
            ]:
                if not _eid:
                    continue
                _st = self.hass.states.get(_eid)
                if _st is None or _st.state in ("unavailable", "unknown"):
                    _LOGGER.error(
                        "battery_manager: verificatie %s MISLUKT — '%s' onbereikbaar na instellen (gevraagd=%dW)",
                        _label, _eid, _req,
                    )
                    continue
                try:
                    _actual = float(_st.state)
                except (ValueError, TypeError):
                    _LOGGER.error(
                        "battery_manager: verificatie %s MISLUKT — '%s' geeft ongeldige waarde '%s' terug",
                        _label, _eid, _st.state,
                    )
                    continue
                if _req == 0:
                    if _actual > 5:
                        _LOGGER.warning(
                            "battery_manager: %s niet op 0W gezet — gevraagd=0W, werkelijk=%.0fW "
                            "(Zendure firmware negeerde commando?)",
                            _label, _actual,
                        )
                elif _actual == 0:
                    _LOGGER.error(
                        "battery_manager: %s VOLLEDIG GEWEIGERD door firmware — gevraagd=%dW, werkelijk=0W "
                        "(entiteit reageert niet of firmware blokkeert commando)",
                        _label, _req,
                    )
                elif _actual < _req * 0.9:
                    _LOGGER.warning(
                        "battery_manager: %s BEPERKT door firmware — gevraagd=%dW, werkelijk=%.0fW "
                        "(firmware-maximum overschreden? Pas DEFAULT_BATTERY_MAX_%s aan in const.py)",
                        _label, _req, _actual,
                        "CHARGE" if _label == "laadlimiet" else "DISCHARGE",
                    )
                else:
                    _LOGGER.info(
                        "battery_manager: %s OK — gevraagd=%dW, werkelijk=%.0fW",
                        _label, _req, _actual,
                    )
            # Mode-verificatie
            _mode_st = self.hass.states.get(mode_entity)
            if _mode_st is None or _mode_st.state in ("unavailable", "unknown"):
                _LOGGER.error(
                    "battery_manager: verificatie AC-mode MISLUKT — '%s' onbereikbaar na instellen "
                    "(gevraagd='%s')",
                    mode_entity, mode_option,
                )
            elif _mode_st.state != mode_option:
                _LOGGER.error(
                    "battery_manager: AC-mode NIET GEWIJZIGD — gevraagd='%s', werkelijk='%s' "
                    "(Zendure negeerde mode-wissel?)",
                    mode_option, _mode_st.state,
                )
            else:
                _LOGGER.info("battery_manager: AC-mode OK — '%s' bevestigd", mode_option)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("battery_manager: batterij-aansturing mislukt: %s", err)

    def _create_schedule(self, all_prices, battery_soc, p1, vacation, min_soc=DEFAULT_MIN_SOC):
        """Look-ahead prijsplanner — kiest de goedkoopste kwartieren om te laden.

        Strategie — puur prijsgestuurd:
          1. Negatieve/gratis prijs  → all_on (altijd laden)
          2. Bereken hoeveel kWh nodig om accu van huidige SOC naar MAX_SOC te brengen
          3. Sorteer toekomstige kwartieren op prijs (goedkoopste eerst)
          4. Ken 'charge' toe aan exact genoeg kwartieren om de accu vol te krijgen
          5. Sorteer op prijs (duurste eerst), ken 'discharge' toe zolang
             prijs > gemiddeld + afschrijving EN accu niet te leeg raakt
          6. Chronologische SOC-check: annuleer ontladen dat de accu onder min_soc brengt
        """
        now_dt = datetime.now()

        # ── Splits verleden / toekomst ───────────────────────────────────
        past = []
        future = []
        for i, quarter in enumerate(all_prices):
            starts_at_raw = quarter.get('startsAt', '')
            try:
                dt = datetime.fromisoformat(
                    starts_at_raw if 'T' in starts_at_raw
                    else starts_at_raw.replace(' ', 'T')
                )
                dt_local = dt.astimezone(tz=None).replace(tzinfo=None)
            except (ValueError, TypeError):
                dt_local = None

            entry = {'idx': i, 'starts_at': starts_at_raw, 'dt': dt_local,
                     'price': quarter.get('total'), 'action': 'normal'}

            if dt_local is not None and dt_local < now_dt - timedelta(minutes=15):
                entry['action'] = 'normal'
                past.append(entry)
            else:
                future.append(entry)

        if vacation:
            for e in future:
                e['action'] = 'forced_off'
            # Geen verdere planning nodig bij vakantie
            return self._build_schedule_result(past, future, battery_soc, min_soc)

        # ── Stap 1: negatieve/gratis prijs altijd all_on ─────────────────
        for e in future:
            if e['price'] is not None and e['price'] <= 0:
                e['action'] = 'all_on'

        # ── Stap 2: bereken laadcapaciteit ───────────────────────────────
        kwh_per_quarter_charge = (DEFAULT_BATTERY_MAX_CHARGE / 1000) * 0.25 * DEFAULT_CHARGE_EFFICIENCY
        soc_per_charge_quarter = (kwh_per_quarter_charge / DEFAULT_BATTERY_CAPACITY) * 100

        # Hoeveel SOC-punten moeten er nog bij via het net?
        # (all_on kwartieren tellen al mee)
        all_on_count = sum(1 for e in future if e['action'] == 'all_on')
        soc_from_all_on = all_on_count * soc_per_charge_quarter
        soc_needed = max(0.0, DEFAULT_MAX_SOC - battery_soc - soc_from_all_on)
        quarters_to_charge = int(soc_needed / soc_per_charge_quarter) + 1 if soc_needed > 0 else 0

        self._dbg(
            "Planner: SOC=%.1f%%, nodig=%.1f%% → %d laadkwartieren via net (all_on=%d)",
            battery_soc, soc_needed, quarters_to_charge, all_on_count,
        )

        # ── Stap 3: kies goedkoopste kwartieren om te laden ──────────────
        # Inclusief het lopende kwartier (gestart < 15min geleden); excl. echte verleden.
        candidates = [
            e for e in future
            if e['action'] == 'normal'
            and e['price'] is not None
        ]
        candidates_sorted_cheap = sorted(candidates, key=lambda e: e['price'])
        for e in candidates_sorted_cheap[:quarters_to_charge]:
            e['action'] = 'charge'

        # ── Stap 3.5: extra arbitrage-cycli ──────────────────────────────
        # Naast het vullen van de accu: zoek extra rendabele laad/ontlaad-
        # paren onder de nog "normal" kwartieren.
        #
        # Break-even formule (per kWh ingekocht van net):
        #   P_sell × η_c × η_d > P_buy + η_c × afschrijving
        #   => P_sell > (P_buy + η_c × afschr) / (η_c × η_d)
        #
        # Alleen uitvoeren als er daadwerkelijk "normal" slots overblijven.
        kwh_per_quarter_discharge = (DEFAULT_BATTERY_MAX_DISCHARGE / 1000) * 0.25 * DEFAULT_CHARGE_EFFICIENCY
        soc_per_discharge_quarter = (kwh_per_quarter_discharge / DEFAULT_BATTERY_CAPACITY) * 100

        def _recompute_soc_at(future_list, start_soc):
            """Helper: bereken SOC aan begin van elk kwartier op basis van huidige acties."""
            soc_map: dict[int, float] = {}
            s = start_soc
            for fe in future_list:
                soc_map[fe['idx']] = s
                if fe['action'] in ('charge', 'all_on'):
                    s = min(DEFAULT_MAX_SOC, s + soc_per_charge_quarter)
                elif fe['action'] == 'discharge':
                    s = max(0.0, s - soc_per_discharge_quarter)
                else:
                    s = max(0.0, s - 0.05)
            return soc_map

        soc_at = _recompute_soc_at(future, battery_soc)

        _arb_used: set[int] = set()
        _buy_pool  = sorted(
            [e for e in future if e['action'] == 'normal' and e['price'] is not None],
            key=lambda x: x['price'],
        )
        _sell_pool = sorted(
            [e for e in future if e['action'] == 'normal' and e['price'] is not None],
            key=lambda x: -x['price'],
        )

        for _buy in _buy_pool:
            if _buy['idx'] in _arb_used:
                continue
            # Accu moet op het koopmomment ruimte hebben
            if soc_at[_buy['idx']] >= DEFAULT_MAX_SOC - 0.1:
                continue
            _breakeven = (_buy['price'] + DEFAULT_CHARGE_EFFICIENCY * BATTERY_DEPRECIATION) / (DEFAULT_CHARGE_EFFICIENCY ** 2)
            # Zoek het duurste verkoopkwartier NA dit kwartier boven break-even
            _best_sell = None
            for _sell in _sell_pool:
                if _sell['idx'] in _arb_used:
                    continue
                if _sell['idx'] <= _buy['idx']:
                    continue
                if _sell['price'] <= _breakeven:
                    break  # gesorteerd desc: alles daarna goedkoper
                _best_sell = _sell
                break
            if _best_sell is None:
                continue
            _buy['action'] = 'charge'
            _best_sell['action'] = 'discharge'
            _arb_used.add(_buy['idx'])
            _arb_used.add(_best_sell['idx'])
            # Herbereken SOC na deze toewijzing zodat de volgende iteratie
            # juiste ruimte-check krijgt
            soc_at = _recompute_soc_at(future, battery_soc)
            self._dbg(
                "Planner arbitrage: laden %s €%.4f → ontladen %s €%.4f (winst €%.4f/kWh input)",
                _buy['starts_at'][11:16], _buy['price'],
                _best_sell['starts_at'][11:16], _best_sell['price'],
                _best_sell['price'] * DEFAULT_CHARGE_EFFICIENCY**2
                - _buy['price'] - DEFAULT_CHARGE_EFFICIENCY * BATTERY_DEPRECIATION,
            )

        # ── Stap 3b: simuleer verwachte SOC per kwartier ─────────────────
        # Inclusief fill-charges (stap 3) én arbitrage-charges (stap 3.5).
        soc_at = _recompute_soc_at(future, battery_soc)
        for e in future:
            e['_proj_soc'] = soc_at[e['idx']]

        # ── Stap 3c: verwijder overbodige laadsloten ─────────────────────
        # Probleem: arb-laadsloten vóór de goedkope fill-periode vullen de
        # accu te vroeg, waardoor de goedkoopste kwartieren nutteloos worden.
        # Oplossing: verwijder laadsloten van duur → goedkoop zolang de accu
        # zonder dat slot nog steeds MAX_SOC bereikt (het is dus overbodig).

        def _peak_soc_sim(future_list, start_soc):
            """Simuleer SOC chronologisch en geef de hoogst bereikte SOC terug."""
            s = start_soc
            peak = s
            for fe in future_list:
                if fe['action'] in ('charge', 'all_on'):
                    s = min(DEFAULT_MAX_SOC, s + soc_per_charge_quarter)
                elif fe['action'] == 'discharge':
                    s = max(0.0, s - soc_per_discharge_quarter)
                if s > peak:
                    peak = s
            return peak

        charge_slots_desc = sorted(
            [e for e in future if e['action'] == 'charge'],
            key=lambda x: -(x['price'] or 0),
        )
        for e in charge_slots_desc:
            e['action'] = 'normal'  # tijdelijk verwijderen
            if _peak_soc_sim(future, battery_soc) >= DEFAULT_MAX_SOC - 0.1:
                # Accu bereikt MAX_SOC ook zonder dit slot → definitief verwijderen
                self._dbg(
                    "Planner 3c: overbodig laadslot verwijderd %s €%.4f",
                    e['starts_at'][11:16], e['price'] or 0,
                )
            else:
                # Dit slot is nodig om MAX_SOC te bereiken → herstellen
                e['action'] = 'charge'

        # Herbereken na cleanup voor de ontlaadplanning
        soc_at = _recompute_soc_at(future, battery_soc)
        for e in future:
            e['_proj_soc'] = soc_at[e['idx']]

        # ── Stap 4: duurste kwartieren ontladen ──────────────────────────
        # Drempel gebaseerd op de wiskundig correcte break-even formule,
        # uitgaande van de goedkoopste laadprijs beschikbaar in het schema.
        # Break-even: P_sell > (P_koop + η_c × afschr) / (η_c × η_d)
        # Dit is altijd scherper dan het oude 'gem + afschr', waardoor meer
        # rendabele ontlaadmomenten worden meegenomen.
        avg_price = (
            sum(e['price'] for e in future if e['price'] is not None) /
            max(1, sum(1 for e in future if e['price'] is not None))
        )
        cheapest_charge_price = min(
            (e['price'] for e in future
             if e['action'] in ('charge', 'all_on') and e['price'] is not None),
            default=avg_price,
        )
        discharge_threshold = (
            (cheapest_charge_price + DEFAULT_CHARGE_EFFICIENCY * BATTERY_DEPRECIATION)
            / (DEFAULT_CHARGE_EFFICIENCY ** 2)
        )

        # ...verwijderd: zonne-drempel/solar-logica...

        total_charge_quarters = sum(1 for e in future if e['action'] in ('charge', 'all_on'))
        estimated_peak_soc = min(DEFAULT_MAX_SOC, battery_soc + total_charge_quarters * soc_per_charge_quarter)
        max_discharge_quarters = max(0, int((estimated_peak_soc - min_soc) / soc_per_discharge_quarter))
        # Trek al geplande ontlaadslots af (uit arbitrage stap 3.5)
        already_discharge = sum(1 for e in future if e['action'] == 'discharge')
        remaining_discharge_slots = max(0, max_discharge_quarters - already_discharge)

        # Kandidaten: nog "normal", duur genoeg én voldoende SOC aanwezig
        discharge_candidates = [
            e for e in future
            if e['action'] == 'normal' and e['price'] is not None
            and e['price'] > discharge_threshold
            and e['_proj_soc'] - soc_per_discharge_quarter >= min_soc
        ]
        discharge_candidates_sorted = sorted(discharge_candidates, key=lambda e: -e['price'])
        for e in discharge_candidates_sorted[:remaining_discharge_slots]:
            e['action'] = 'discharge'

        self._dbg(
            "Planner: goedkoopste_laad=€%.4f, ontlaaddrempel=€%.4f, max=%d kwartieren (+%d arb), kandidaten=%d",
            cheapest_charge_price, discharge_threshold,
            remaining_discharge_slots, already_discharge, len(discharge_candidates),
        )

        # ── Stap 4b: chronologische SOC-simulatie (veiligheidsnet) ───────
        # De proj_soc filtering hierboven sluit structureel al te-vroeg-ontladen
        # uit, maar we houden de simulatie als extra check.
        sim_soc = battery_soc
        for e in future:
            act = e['action']
            if act in ('charge', 'all_on'):
                sim_soc = min(DEFAULT_MAX_SOC, sim_soc + soc_per_charge_quarter)
            elif act == 'discharge':
                soc_after = sim_soc - soc_per_discharge_quarter
                if soc_after < min_soc:
                    e['action'] = 'normal'
                    self._dbg(
                        "Planner: ontladen geannuleerd op %s (SOC=%.1f%% zou %.1f%% worden < min %.1f%%)",
                        e['starts_at'], sim_soc, soc_after, min_soc,
                    )
                else:
                    sim_soc = soc_after
            else:
                sim_soc = max(0.0, sim_soc - 0.05)

        # ── Diagnose schrijven NA alle correcties ─────────────────────────
        n_charge    = sum(1 for e in future if e['action'] in ('charge', 'all_on'))
        n_discharge = sum(1 for e in future if e['action'] == 'discharge')
        _LOGGER.info(
            "battery_manager planner: SOC=%.1f%% | laden=%d kwartieren | ontladen=%d kwartieren | goedkoopste_laad=€%.4f | drempel=€%.4f",
            battery_soc, n_charge, n_discharge, cheapest_charge_price, discharge_threshold,
        )
        try:
            diag_lines = [
                f"=== battery_manager planner {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===",
                f"SOC={battery_soc:.1f}%  min_soc={min_soc}%",
                f"cheapest_charge=€{cheapest_charge_price:.4f}  discharge_threshold=€{discharge_threshold:.4f}  BATTERY_DEPRECIATION=€{BATTERY_DEPRECIATION:.4f}",
                f"quarters_to_charge={quarters_to_charge}  arb_pairs={len(_arb_used)//2}  max_discharge_quarters={max_discharge_quarters}",
                f"n_charge={n_charge}  n_discharge={n_discharge}",
                "--- toekomstige acties (na alle correcties) ---",
            ]
            for e in future[:96]:
                t = e.get('starts_at', '')
                try:
                    t_short = t[11:16]
                except Exception:
                    t_short = t
                diag_lines.append(
                    f"  {t_short}  €{e['price']:.4f}  {e['action']}"
                    if e['price'] is not None else
                    f"  {t_short}  None  {e['action']}"
                )
            self._pending_diag = "\n".join(diag_lines)
        except Exception:
            self._pending_diag = None

        return self._build_schedule_result(past, future, battery_soc, min_soc)

    def _build_schedule_result(self, past, future, battery_soc, min_soc):
        """Combineer past + future tot een schedule-lijst met gesimuleerde SOC."""
        kwh_per_charge    = (DEFAULT_BATTERY_MAX_CHARGE / 1000) * 0.25 * DEFAULT_CHARGE_EFFICIENCY
        kwh_per_discharge = (DEFAULT_BATTERY_MAX_DISCHARGE / 1000) * 0.25 * DEFAULT_CHARGE_EFFICIENCY

        result = []
        for e in past:
            result.append({
                'quarter':    e['idx'],
                'starts_at':  e['starts_at'],
                'price':      e['price'],
                'action':     'normal',
                'battery_soc': None,
            })

        simulated_soc = battery_soc
        for e in future:
            action = e['action']
            if action in ('charge', 'all_on'):
                simulated_soc = min(DEFAULT_MAX_SOC,
                    simulated_soc + (kwh_per_charge / DEFAULT_BATTERY_CAPACITY) * 100)
            elif action == 'discharge':
                # Clamp op min_soc maar pas de geplande actie NIET aan —
                # de planner heeft al geborgd dat het totaal aantal ontlaadkwartieren
                # past bij de beschikbare energie. De hardware heeft ook een eigen
                # min-SOC beveiliging.
                simulated_soc = max(min_soc,
                    simulated_soc - (kwh_per_discharge / DEFAULT_BATTERY_CAPACITY) * 100)
            else:
                simulated_soc = max(0.0, simulated_soc - 0.05)

            result.append({
                'quarter':    e['idx'],
                'starts_at':  e['starts_at'],
                'price':      e['price'],
                'action':     action,
                'battery_soc': round(simulated_soc, 1),
            })

        return result

