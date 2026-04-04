## Development environment constraints (IMPORTANT - read before debugging)
- **Language**: Always respond in English
- **No Python available** on SSH host — do not run `python`/`python3` commands
- **HA runs in a container** — log files are inside the container, not directly accessible via SSH at `/root/config/`
- **Log access**: Use the HA web UI: Settings → System → Logs, or filter at http://ha.gourami.nl:48123/config/logs
- **Log access via SSH** (HAOS): `/config/home-assistant.log` does NOT exist as a file on disk during runtime. Use the Supervisor API instead:
  ```
  curl -s -H "Authorization: Bearer $SUPERVISOR_TOKEN" http://supervisor/core/logs | grep battery_manager | tail -50
  ```
  `$SUPERVISOR_TOKEN` is available as an environment variable in the SSH session. The rotated logs `/config/home-assistant.log.1` and `.log.old` are readable but only contain old sessions (before current boot).
- **Config entry reload ≠ module reimport**: When reloading an integration via UI, HA does NOT reimport Python modules (they stay in `sys.modules`). A full **HA restart** is required to pick up any Python code changes. Check `.pyc` timestamps vs `.py` timestamps to detect stale cache: `ls -la __pycache__/` vs `ls -la *.py`
- **Testing battery control**: Use Developer Tools → Actions → `battery_manager.force_action` with `data: {action: discharge}` — but only after a full HA restart following code changes

---


# Battery Manager – Specificatie

Deze integratie bestuurt een thuisbatterij volledig prijsgestuurd. De logica is puur gericht op het optimaliseren van laden en ontladen op basis van actuele en verwachte stroomprijzen.

## Instelbare parameters
| Parameter              | Standaard  | Beschrijving                       |
|------------------------|------------|------------------------------------|
| Minimum SOC            | 10 %       | Ondergrens batterij (ontladen stopt) |
| Maximum SOC            | 100 %      | Bovengrens batterij (laden stopt)    |
| Laad-efficiëntie       | 90 %       | Verlies bij AC→DC omzetting         |
| Accu-afschrijving      | auto       | Automatisch berekend per kWh        |
| Accu-levensduur        | 6000 cycli | Bepaalt afschrijving per kWh        |



## Plannerlogica

De planner kijkt 48 uur vooruit en bepaalt per kwartier de optimale actie:
1. Negatieve/gratis prijs → actie `all_on` (altijd laden)
2. Laadkwartieren: selecteer exact genoeg kwartieren om de accu vol te krijgen (puur op prijs)
3. Arbitrage: zoek onder de resterende kwartieren naar rendabele koop/verkoop-paren (break-even formule)
4. Ontlaadkwartieren: selecteer resterende kwartieren waarbij `prijs > break-even(koopprijs)`
5. Chronologische check: simuleer SOC per kwartier; annuleer ontladen dat onder minimum SOC zou komen
6. Live veiligheidscheck: als actuele SOC ≤ min_soc → ontladen geblokkeerd

### Debugging en SOC-projectie
- De SOC-projectie (witte lijn) wordt per kwartier gesimuleerd op basis van het laadplan en de ingestelde limieten.
- In de Home Assistant logs wordt nu per kwartier gelogd: het laadplan, de start-SOC, de limieten, en voor elk kwartier de actie en SOC-verandering.
- Dit maakt het eenvoudig te controleren of de projectie overeenkomt met het verwachte laadgedrag.


| Actie        | AC Mode        | Laadlimiet | Ontlaadlimiet | Wanneer                |
|--------------|----------------|------------|---------------|-----------------------|
| `charge`     | AC Input Mode  | 800 W      | 0 W           | Accu vullen naar MAX   |
| `all_on`     | AC Input Mode  | 800 W      | 0 W           | Prijs ≤ €0,00          |
| `discharge`  | AC Output Mode | 0 W        | 800 W         | Ontladen boven drempel |
| `forced_off` | AC Input Mode  | 0 W        | 0 W           | Vakantie               |
| `normal`     | AC Input Mode  | 0 W        | 0 W           | Standby                |






## Prijsdata

De integratie haalt stroomprijzen op via de Tibber GraphQL API (`https://api.tibber.com/v1-beta/gql`).

### Kwartierprijzen (standaard sinds okt 2025)
Sinds 1 oktober 2025 levert de EPEX day-ahead markt kwartierprijzen (96 per dag). De Tibber API ondersteunt dit via een `resolution` parameter op `priceInfo`:
```graphql
priceInfo(resolution: QUARTER_HOURLY) {
  today { startsAt total energy tax }
  tomorrow { startsAt total energy tax }
}
```
- De integratie probeert **altijd eerst** kwartierprijzen op te halen.
- **Fallback:** Als `QUARTER_HOURLY` niet beschikbaar is (foutmelding van Tibber), wordt teruggevallen op uurprijzen die vervolgens geëxpandeerd worden naar 4 × 15-minuten kwartieren met dezelfde prijs.
- Bron: https://developer.tibber.com/docs/changelog (entry 2025-09-01)


## Regels
1. Prijs is de enige motivatie voor laad- en ontlaadbeslissingen. Houd rekening met afschrijving en efficiëntie.
2. Vóór een negatieve-prijsperiode moet de accu zo leeg mogelijk zijn zodat er maximaal geladen kan worden.
3. Houd rekening met terugleverkosten.




## Logging & observeerbaarheid
Elke beslissing die de integratie neemt moet herleidbaar zijn uit de HA-logs:
- **INFO** elke 15 minuten: actief kwartier, prijs en gekozen actie
- **INFO** elke 15 minuten: plannerresultaat (SOC, aantal laad- en ontlaadkwartieren, drempelprijs)
- **WARNING** bij actiewijziging: van welke actie naar welke, op welk kwartier, tegen welke prijs
- **WARNING** bij geblokkeerd ontladen: live SOC vs. min_soc
- Diagnosebestand `/config/battery_manager_diag.txt` met volledig 48u schema na elke update

Thread-safety vereisten (asyncio)
- Alle file I/O (`read_text`, `write_text`) mag nooit op de event loop worden uitgevoerd; altijd via `hass.async_add_executor_job()`.
- Event-listeners die `hass.async_create_task()` aanroepen moeten `async def` zijn.
- `__init__` van de coordinator mag geen blocking I/O bevatten; deferred naar de eerste `_async_update_data`.





