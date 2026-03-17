# Battery Manager – Voortgang

Laatste update: 15 maart 2026

---

## Wat is gebouwd

### Integratie-basis (volledig werkend)
- `manifest.json` – HA-manifest, versie 0.1.0, config_flow aan
- `__init__.py` – setup/unload; kopieert bij elke opstart de frontend JS naar `/config/www/battery_manager/` zodat HA het serveert via `/local/battery_manager/battery-dashboard.js`
- `const.py` – alle constanten: batterijcapaciteit, laad/ontlaadsnelheid, kosten, cycli, efficiëntie, SOC-limieten

### Config Flow (`config_flow.py`) – 3-stappen UI-wizard
1. **Sensoren** – Tibber token, batterij SOC, P1-meter, vakantie schakelaar
2. **Apparaten** – batterij modus-select, laad- en ontlaadlimiet
3. **Beheer** – batterijsturing aan/uit

### Coordinator (`coordinator.py`) – pollt elke 15 minuten
- Haalt Tibber kwartierrijzen rechtstreeks op via GraphQL API
- `_create_schedule()` – berekent acties per kwartier voor vandaag + morgen:
  - `all_on` – negatieve/gratis prijs, altijd laden
  - `charge` – laden tot MAX_SOC
  - `discharge` – ontladen boven drempel
  - `forced_off` – vakantie, niets aansturen
  - `normal` – standby
- Extra arbitrage-cycli: zoekt rendabele koop/verkoop-paren buiten de vul-charge
- Chronologische SOC-simulatie voorkomt dat ontladen de accu onder `min_soc` brengt
  - `_build_chart_data()` – per-kwartier dataset: prijs (€/kWh), SOC-projectie (%)
- `_apply_battery_control()` – stuurt Zendure-batterij aan via select + number entiteiten
- Live SOC-veiligheidscheck: ontladen geblokkeerd als actuele SOC ≤ `min_soc`
- SOC-cache overleeft HA-herstart (`.storage/battery_manager_soc_cache.json`)
- Diagnosebestand na elke update: `/config/battery_manager_diag.txt`

### Sensoren (`sensor.py`)
| Sensor | Inhoud |
|---|---|
| `sensor.battery_manager_battery_soc` | Huidige SOC (%) |
| `sensor.battery_manager_current_action` | Actie huidig kwartier |
| `sensor.battery_manager_current_price` | Prijs huidig kwartier (€/kWh) |
| `sensor.battery_manager_chart` | Aantal kwartieren; attribuut `chart_data` = volledige 48u lijst |

### Frontend (`frontend/battery-dashboard.js`)
- Lovelace custom card `battery-dashboard-card`
- Chart.js geladen van CDN (v4.4.0)
- Gecombineerd chart:
  - Prijs (lichtgroen `#86efac`) – lijn, rechter-as
  - Batterij SOC (wit) – lijn, rechter-as 0–100%, vanaf huidig moment
- Gekleurde achtergrond per kwartier op basis van actie
- Rode stippellijn op huidig tijdstip ("nu")
- Fullscreen modal bij klikken op grafiek (sluiten via ✕, achtergrond of Escape)
- Automatische cache-busting via MD5-hash in resource URL
  - Tooltips met prijs, accu-% en actie in het Nederlands

**Lovelace resource URL:** `/local/battery_manager/battery-dashboard.js?v=<hash>`

**Kaart YAML:**
```yaml
type: custom:battery-dashboard-card
entity: sensor.battery_manager_chart
title: "Batterij Planning – komende 48 uur"
```

---

## Wijzigingen sessie 7 (15 maart 2026)

### Firmware-update Zendure: max laad/ontlaadsnelheid 2400W → 800W

**Symptoom:** Batterij laadt/ontlaadt niet (terugkerend, meermaals gemeld).

**Oorzaak:** Zendure firmware-update heeft het maximum AC-laad en -ontlaadvermogen verlaagd van 2400W naar 800W. De integratie stuurde 2400W — firmware accepteerde het commando maar beperkte stil.

**Fix:**
- `const.py`: `DEFAULT_BATTERY_MAX_CHARGE` en `DEFAULT_BATTERY_MAX_DISCHARGE`: `2400` → `800`
- Alle referenties in `SPEC.MD` en `fgvc.agent.md` bijgewerkt

**Gevolg voor planning:** Laad/ontlaadenergie per kwartier: `0.18 kWh` (was `0.54 kWh`). Planner itereert meer kwartieren voor dezelfde SOC-winst — dit werkt automatisch met de nieuwe constante.

### Firmware-verificatie na elk aansturingscommando (`coordinator.py`)

Na elke `set_value`/`select_option`-aanroep wacht de code 2 seconden en leest de werkelijke waarde terug:
- `ERROR` als entiteit onbereikbaar is na instellen
- `ERROR` als gevraagde waarde > 0 maar werkelijk = 0 (firmware weigert volledig)
- `WARNING` als werkelijk < 90% van gevraagd (firmware-cap actief)
- `ERROR` als AC-mode niet overgenomen
- `INFO` als alles klopt

### RCA-logboek aangemaakt (`RCA-info.md`)
- Nieuw bestand met 8 gedocumenteerde oorzaken + diagnosestappen + logtabel
- Uitleg hoe logs te lezen (HA UI, SSH/grep, debug logging via `configuration.yaml`)

### Kritieke logberichten opgewaardeerd INFO → WARNING
- `batterijsturing uitgeschakeld (CONF_MANAGE_BATTERY=False)` — was onzichtbaar in HA-logboek
- `actief kwartier HH:MM → actie='...'` — nu altijd zichtbaar
- `batterij actie '...' -> mode=..., laden=...W, ontladen=...W` — nu altijd zichtbaar

---

## Wijzigingen sessie 6 (13 maart 2026)

### Bugfix: accu ontlaadt niet wanneer SOC hoog is door zonnelading

**Symptoom:** Accu stond al dag op ~99% SOC (gevuld door zon), maar er werden geen ontlaadkwartieren gepland, terwijl avondprijzen (€0.33-€0.33) ruim boven afschrijvingsniveau lagen.

**Oorzaak (deadlock in drempelberekening):**
- Planner ziet 1% ruimte naar MAX_SOC → wijst 1 laadkwartier toe (goedkoopste komend etmaal, bijv. morgen 13:00 @ €0.26)
- Discharge-drempel wordt berekend als `(P_koop + η_c × afschr) / η_c² = (0.26 + 0.9×0.048) / 0.81 = €0.3746`
- Avondprijzen €0.33 liggen onder die drempel → nul ontlaadslots
- Resultaat: accu blijft vol, morgen wordt toch bijgeladen via net, maar zon vult dan diezelfde dag opnieuw

**Fix: zonne-drempel override (`coordinator.py`, stap 4):**
- Als `battery_soc ≥ 85%` is de energie hoogstwaarschijnlijk gratis via de zon binnengekomen
- Herlaadkost na ontladen is dan effectief €0 (zon vult de volgende dag bij)
- Enige relevante kost is afschrijving; drempel wordt: `afschrijving / η_d = €0.048 / 0.9 = €0.054/kWh`
- Bij €0.054 drempel worden alle avondkwartieren boven €0.054 als discharge ingepland (in de praktijk alles boven ~€0.15)
- Winst bij bijv. €0.33: `0.33 × 0.9 − 0.048 ≈ €0.25/kWh`
- Diagnosebestand toont `[zonne-drempel]` aan de drempelregel als override actief is

**Te verifiëren morgen (14 maart):**
- Zie log of diag: `discharge_threshold=€0.05xx  [zonne-drempel]`
- `n_discharge` moet > 0 zijn in de avond/nachturen
- Actie-log toont `ACTIE GEWIJZIGD 'normal' → 'discharge'` op avondkwartieren

---

## Wijzigingen sessie 5 (7 maart 2026)

### Thread-safety fixes (HA-waarschuwingen opgelost)
- `_on_soc_change` in `__init__.py` was een gewone `def` — HA voert die uit in een thread-pool. Aanroep van `hass.async_create_task()` vanuit een thread kan HA laten crashen of data corrumperen. Opgelost door `async def` te maken (HA schedult async listeners als event-loop tasks).
- `_save_soc_cache(live)` werd direct aangeroepen vanuit diezelfde thread (blokkerende file I/O). Opgelost met `await hass.async_add_executor_job(...)`.
- `_load_soc_cache` in `coordinator.__init__` werd eerder direct aangeroepen (blokkerende `read_text` op de event loop). Was al verplaatst naar de eerste `_async_update_data` via executor; melding verdwijnt na herstart.

### Verbeterde logging voor batterijsturing
- `_find_current_action()` geeft nu het volledige slot-dict terug (was: alleen de actie-string) zodat tijdstip en prijs beschikbaar zijn.
- `_apply_battery_control()` logt nu bij elk kwartier:
  - **INFO**: actief kwartier, prijs en actie — bijv. `actief kwartier 14:15 (€0.1823/kWh) → actie='discharge'`
  - **WARNING**: wanneer de actie verandert t.o.v. de vorige cyclus — bijv. `ACTIE GEWIJZIGD 'discharge' → 'normal'`
  - **WARNING**: wanneer ontladen geblokkeerd wordt door te lage live-SOC — bijv. `ontladen GEBLOKKEERD op 17:45 — live SOC 9.8% ≤ min_soc 10.0%`
  - **INFO**: wanneer actie onveranderd is en geen commando verstuurd wordt

---

## Wijzigingen sessie 4 (6 maart 2026)

### Hernoemd naar Battery Manager
- Map hernoemd: `energy_manager/` → `battery_manager/`
- Domain: `energy_manager` → `battery_manager`
- JS-kaart: `energy-dashboard-card` → `battery-dashboard-card`
- JS-bestand: `energy-dashboard.js` → `battery-dashboard.js`
- Alle klasse- en sensornames bijgewerkt

### Opschoning niet-batterij functionaliteit
- Zonnepanelen (Forecast.Solar, sensor, grafiekdata) verwijderd
- Warmtepomp-aansturing en `heatpump_on` actie verwijderd
- Autolader-schakelaar verwijderd
- Weersverwachting en koude-dag logica verwijderd
- Config flow: alleen nog batterij-relevante velden
- SPEC.MD bijgewerkt naar huidige scope

---

## Status (15 maart 2026)

- [x] Integratie laadt en herstart correct
- [x] Config flow werkt (3 stappen) incl. Tibber token validatie
- [x] Coordinator haalt Tibber prijzen rechtstreeks op via API
- [x] Coordinator berekent schema + chart data
- [x] Batterij wordt daadwerkelijk aangestuurd via select/number entiteiten
- [x] Live SOC-veiligheidscheck bij ontladen
- [x] SOC-cache overleeft HA-herstart
- [x] Alle sensoren aanwezig, uitgesloten van energy dashboard
- [x] Frontend JS automatisch gedeployed met cache-busting hash
  - [x] Grafiek werkt — prijs (groen), SOC (wit)
- [x] Fullscreen modal bij klikken op grafiek
- [x] Debug logging via opties-schakelaar
- [x] Zonnepanelen, warmtepomp, boiler en autolader verwijderd
- [x] Hernoemd van Energy Manager naar Battery Manager
- [x] Thread-safety: async_create_task + blocking I/O niet meer op event loop
- [x] Logging: elke actiewijziging en geblokkeerd ontladen zichtbaar als WARNING in HA-logs
- [x] Zonne-drempel override: bij SOC ≥ 85% wordt ontlaaddrempel gebaseerd op afschrijving i.p.v. herlaadkost
- [x] Firmware-update Zendure: max laad/ontlaad 2400W → 800W doorgevoerd in const.py
- [x] Verificatie na commando: werkelijke waarde teruggelezen, afwijking gelogd als WARNING/ERROR
- [x] Kritieke INFO-logs opgewaardeerd naar WARNING (nu altijd zichtbaar)
- [x] RCA-info.md aangemaakt met 8 bekende oorzaken + diagnosestappen
- [ ] **VANAVOND VERIFIËREN:** laden/ontladen daadwerkelijk uitgevoerd? (logs + diag checken)

---

## TODO – volgende sessie

- [ ] Verifieer firmware-fix vanavond (15 maart): controleer logs op `laadlimiet OK — gevraagd=800W, werkelijk=800W` en `batterij-aansturing geslaagd`
- [ ] Verifieer zonne-drempel fix (controleer diag + logs op `[zonne-drempel]` en `n_discharge > 0`)
- [ ] Terugleverkosten meenemen in ontlaaddrempel
- [ ] Negatieve prijs look-ahead: accu vooraf leeg rijden voor maximale laadcapaciteit

---

## Bestandsstructuur
```
battery_manager/
├── __init__.py          – setup + frontend deploy
├── config_flow.py       – 3-stappen configuratiewizard
├── const.py             – alle constanten
├── coordinator.py       – data ophalen + schema + chart data
├── sensor.py            – 4 HA-sensoren
├── manifest.json
├── SPEC.MD
├── VOORTGANG.md
└── frontend/
    └── battery-dashboard.js   – Lovelace custom card
```
