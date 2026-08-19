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

De planner plant op basis van alle beschikbare toekomstige prijzen. Zodra Tibber de prijzen voor morgen beschikbaar stelt (~13:00-14:00), plant de planner automatisch voor vandaag én morgen (tot 48 uur vooruit).

De planner moet plannen op basis van winst uitgedrukt in geld. Dat wil zeggen dat hij zo veel mogelijk moet laden en ontladen zolang het geld oplevert. Rekening houden met de 90% verlies. 

## Regels
1. Prijs is de enige motivatie voor laad- en ontlaadbeslissingen. Houd rekening met afschrijving en efficiëntie.
2. Vóór een negatieve-prijsperiode moet de accu zo leeg mogelijk zijn zodat er maximaal geladen kan worden.
3. Houd rekening met terugleverkosten.








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





