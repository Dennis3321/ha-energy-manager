DOMAIN = "battery_manager"

# Battery defaults
DEFAULT_BATTERY_CAPACITY = 8.64
DEFAULT_BATTERY_MAX_CHARGE = 2400
DEFAULT_BATTERY_MAX_DISCHARGE = 2400
DEFAULT_BATTERY_COST = 2500
DEFAULT_BATTERY_CYCLES = 6000   # LFP levensduur; verdubbeld t.o.v. ouder standaard
DEFAULT_CHARGE_EFFICIENCY = 0.90
DEFAULT_MIN_SOC = 10            # Fabrikant minimum (Zendure SolarEdge 2400)
DEFAULT_MAX_SOC = 100

# Price threshold defaults
DEFAULT_CHEAP_FACTOR = 0.85
DEFAULT_EXPENSIVE_FACTOR = 1.05  # Ruimere ontlaad-drempel (was 1.15)

# Options-flow keys (aanpasbaar zonder herinstallatie)
CONF_MIN_SOC = "min_soc"

# Config entry keys
CONF_TIBBER_TOKEN = "tibber_token"
CONF_BATTERY_SOC = "battery_soc"
CONF_P1_METER = "p1_meter"
CONF_BATTERY_MODE = "battery_mode"
CONF_BATTERY_CHARGE_LIMIT = "battery_charge_limit"
CONF_BATTERY_DISCHARGE_LIMIT = "battery_discharge_limit"
CONF_MANAGE_BATTERY = "manage_battery"
CONF_DEBUG_LOGGING = "debug_logging"
