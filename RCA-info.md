# RCA-info — Battery Manager: Batterij laadt/ontlaadt niet

## Terugkerend probleem (meermaals gemeld)

**Symptoom:** De batterij laadt niet op en/of ontlaadt niet, ook al zijn de prijzen gunstig of zou het verwacht worden op basis van het schema.

---

## Bekende oorzaken & fixes (historisch overzicht)

### RCA-1 — `CONF_MANAGE_BATTERY` staat uit (meest voorkomend)
- **Oorzaak:** `_apply_battery_control()` keert direct terug als `config.get(CONF_MANAGE_BATTERY, False)` `False` is.
- **Log:** `battery_manager: batterijsturing uitgeschakeld (CONF_MANAGE_BATTERY=False)`
- **Fix:** Ga naar Instellingen → Integraties → Battery Manager → Opties → schakel "Batterijsturing inschakelen" aan.

---

### RCA-2 — Geen mode-entiteit geconfigureerd
- **Oorzaak:** `CONF_BATTERY_MODE` is leeg → geen commando verstuurd.
- **Log:** `battery_manager: geen mode-entiteit geconfigureerd, batterij niet aangestuurd`
- **Fix:** Open de config flow (Integraties → herconfigureren) en kies de juiste `select`-entiteit voor de Zendure AC-modus.

---

### RCA-3 — Zendure-entiteiten zijn `unavailable` / `unknown`
- **Oorzaak:** De Zendure MQTT/Bluetooth-integratie is (tijdelijk) offline. Na 3 retry-pogingen (elk 10 seconden wachten) wordt de actie overgeslagen.
- **Log:** `battery_manager: Zendure-entiteiten blijven unavailable na 3 pogingen, actie '...' overgeslagen: ...`
- **Fix:** Controleer of de Zendure-integratie zelf actief is en de entiteiten een geldige waarde tonen in HA. Herstart de integratie indien nodig.

---

### RCA-4 — Ontlaaddrempel te hoog door goedkoop toekomstig laadkwartier (deadlock)
- **Oorzaak (sessie 6, 13 maart 2026):** De planner zag een goedkoop laadkwartier morgen (bijv. €0.26), berekende daarmee een hoge ontlaaddrempel (`€0.3746`). Avondprijzen (bijv. €0.33) lagen net onder de drempel → nul ontlaadslots, terwijl de accu al vol was via de zon.
- **Formule:** `discharge_threshold = (P_koop + η_c × afschr) / η_c²`
- **Fix (zonne-drempel override):** Als `battery_soc ≥ 85%` wordt de drempel verlaagd naar `afschrijving / η_d ≈ €0.054`. Alle kwartieren boven ~€0.05 worden dan ingepland voor ontladen.
- **Diagnosekenmerk:** `/config/battery_manager_diag.txt` toont `[zonne-drempel]` achter de drempelregel.

---

### RCA-5 — Live SOC ≤ min_soc blokkeert ontladen
- **Oorzaak:** Hardware-veiligheidscheck in `_apply_battery_control()`: als de actuele SOC op of onder `min_soc` staat, wordt de actie omgezet naar `normal`.
- **Log:** `battery_manager: ontladen GEBLOKKEERD op HH:MM — live SOC X.X% ≤ min_soc Y.Y% → teruggevallen op 'normal'`
- **Fix:** Wacht tot de batterij opgeladen is, of verlaag `min_soc` via Opties als de geconfigureerde waarde te hoog is.

---

### RCA-6 — Accu al op MAX_SOC → geen laadsloten ingepland
- **Oorzaak:** `soc_needed = max(0, MAX_SOC − battery_soc − soc_from_all_on)` is 0 of bijna 0 → `quarters_to_charge = 0`.
- **Gevolg:** Geen laadkwartieren in het schema; als ook de arbitrage geen paren vindt (te krappe marges), geen enkele actie.
- **Log (diag):** `n_charge=0  n_discharge=0`
- **Fix:** Geen actie vereist als de accu vol is. Controleer wel of de ontlaadkwartieren er zijn (zie RCA-4).

---

### RCA-8 — Firmware-update: maximum laad/ontlaadsnelheid verlaagd naar 800W
- **Oorzaak (maart 2026):** Zendure firmware-update heeft het maximum van 2400W naar 800W verlaagd. De software stuurde 2400W — de firmware accepteerde dit commando maar beperkte de werkelijke output stil.
- **Log (nieuw, na fix):** `battery_manager: laadlimiet BEPERKT door firmware — gevraagd=2400W, werkelijk=800W (firmware-maximum overschreden? Pas DEFAULT_BATTERY_MAX_CHARGE aan in const.py)`
- **Fix:** `const.py`: `DEFAULT_BATTERY_MAX_CHARGE` en `DEFAULT_BATTERY_MAX_DISCHARGE` van 2400 → 800. **Doorgevoerd op 15 maart 2026.**
- **Gevolg voor planning:** Laad/ontlaadcapaciteit per kwartier is nu 0.18 kWh (was 0.54 kWh). De planner itereert meer kwartieren om dezelfde SOC te bereiken.

---


- **Oorzaak:** Bewuste optimalisatie: als de actie niet veranderd is én het is geen laad/ontlaadactie, wordt geen commando verstuurd.
- **Log:** `battery_manager: actie 'normal' onveranderd t.o.v. vorige cyclus — geen commando verstuurd`
- **Gevolg:** Normaal geen probleem. Problematisch als de Zendure zijn eigen modus reset terwijl HA denkt dat het al goed staat.
- **Fix:** Herstart de Battery Manager integratie om `_last_applied_action` te resetten, of pas de code aan zodat laad/ontlaad-acties altijd verstuurd worden (dat is al geïmplementeerd — alleen `normal`/`forced_off` slaan over).

---

## Hoe de logs lezen

### Optie A — HA UI (makkelijkst)
`Instellingen → Systeem → Logboek` → zoek bovenin naar `battery_manager`.  
Standaard toont HA alleen **WARNING** en hoger. Alle berichten hieronder gemarkeerd met ⚠️ zijn altijd zichtbaar.

### Optie B — SSH / terminal
```bash
grep battery_manager /config/home-assistant.log | tail -50
# of live meekijken:
tail -f /config/home-assistant.log | grep battery_manager
```

### Optie C — debug logging aanzetten (alles zien, ook INFO/DEBUG)
Voeg toe aan `/config/configuration.yaml`:
```yaml
logger:
  default: warning
  logs:
    custom_components.battery_manager: debug
```
Herstart HA daarna. Dit maakt ook de `[EM-DEBUG]`-berichten zichtbaar (dezelfde schakelaar als de debug_logging optie in de integratie).

**Let op:** INFO-berichten zijn standaard **niet zichtbaar** in het HA-logboek zonder bovenstaande logger-config.

---

## Logberichten per situatie (niveau & zichtbaarheid)

| Situatie | Niveau | Altijd zichtbaar? | Log-tekst |
|---|---|---|---|
| Batterijsturing uitgeschakeld | ⚠️ WARNING | ja | `batterijsturing uitgeschakeld (CONF_MANAGE_BATTERY=False)` |
| Geen mode-entiteit | ⚠️ WARNING | ja | `geen mode-entiteit geconfigureerd` |
| Actief kwartier + beslissing | ⚠️ WARNING | ja | `actief kwartier HH:MM (€X.XXXX/kWh) → actie='...'` |
| Actie + doelwaarden | ⚠️ WARNING | ja | `batterij actie '...' -> mode=..., laden=...W, ontladen=...W` |
| Actie gewijzigd | ⚠️ WARNING | ja | `ACTIE GEWIJZIGD '...' → '...' op kwartier ...` |
| Ontladen geblokkeerd (SOC te laag) | ⚠️ WARNING | ja | `ontladen GEBLOKKEERD op HH:MM — live SOC ...% ≤ min_soc ...%` |
| Laad/ontlaadlimiet instellen | ⚠️ WARNING | ja | `stel laadlimiet in → entity = XXX W` |
| AC mode instellen | ⚠️ WARNING | ja | `stel AC mode in → entity = 'input'/'output'` |
| Aansturing geslaagd | ⚠️ WARNING | ja | `batterij-aansturing geslaagd: actie='...'` |
| Aansturing mislukt | ⚠️ WARNING | ja | `batterij-aansturing mislukt: ...` |
| Zendure entities unavailable | ⚠️ WARNING | ja | `Zendure-entiteiten blijven unavailable na 3 pogingen` |
| Tibber API geen data | ⚠️ WARNING | ja | `Tibber API returned no prices` / `Tibber API error` |
| Actie onveranderd (geen cmd) | ℹ️ INFO | nee* | `actie '...' onveranderd t.o.v. vorige cyclus` |
| SOC-cache geladen | ℹ️ INFO | nee* | `SOC-cache geladen: XX.X%%` |
| Planner samenvatting | ℹ️ INFO | nee* | `battery_manager planner: SOC=...` |

*Alleen zichtbaar met `custom_components.battery_manager: debug` in `configuration.yaml`.

---

## Standaard diagnosestappen

1. **Controleer `/config/battery_manager_diag.txt`** — meest recente planner-output met SOC, drempels, n_charge, n_discharge en per-kwartier acties.
2. **Controleer HA-logs** — zie "Hoe de logs lezen" hierboven. Zoek op `ACTIE GEWIJZIGD`, `GEBLOKKEERD`, `unavailable`, `uitgeschakeld`, `geslaagd`.
3. **Als er géén battery_manager regels in de log staan:** de coordinator runt niet. Controleer of de integratie geladen is (`Instellingen → Integraties`) en herstart HA.
4. **Controleer `sensor.battery_manager_current_action`** — toont de verwachte huidige actie.
5. **Controleer of Zendure-entiteiten online zijn** in HA Developer Tools → States.
6. **Verifieer dat `CONF_MANAGE_BATTERY = True`** in de integratie-opties.

---

## Versiehistorie van dit probleem

| Datum | Sessie | Symptoom | Oorzaak | Fix |
|---|---|---|---|---|
| 13 mrt 2026 | 6 | Accu ontlaadt niet bij SOC ~99% en avondprijzen €0.33 | Deadlock drempelberekening (RCA-4) | Zonne-drempel override bij SOC ≥ 85% |
| 7 mrt 2026 | 5 | Verbeterde logging toegevoegd voor diagnose | n.v.t. | WARNING-logs bij actiewijziging en blokkering |
| 15 mrt 2026 | huidig | Batterij laadt én ontlaadt niet | Onbekend — voer diagnosestappen uit | Zie boven |

---

*Dit document wordt bijgehouden als terugkerende RCA-log. Voeg nieuwe instanties toe aan de versiehistorie.*
