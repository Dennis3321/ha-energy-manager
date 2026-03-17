---
description: 'Describe what this custom agent does and when to use it.'
tools: []
---
Ik wil zelf software maken om mijn thuis accu, zonnepanelen, heat pump, boiler en autolader to besturen volgende deze regels;
Constanten
-Type Accu Zendure SolarEdge 2400, kosten 2500 euro
-capacitiet accu; 8.64 kwh
-maximale laadsnelheid; 800 watt (firmware-update maart 2026: limiet verlaagd van 2400 naar 800W)
-maximale ontlaadsnelheid; 800 watt (firmware-update maart 2026: limiet verlaagd van 2400 naar 800W)
-DHW staat voor Domestic Hot Water = de boiler
-De boiler heeft een 80 liter tank

De warmtepomp heeft 4 modi; (Te zetten via; select.m5poe_heatpump_smartgrid)
1-"Free running" optimaliseer stroomgebruik, gebruik alleen als nodig
2-"Forced off" geen stroom gebruik
3-"Recommended on" gebruik stroom voor verhoogt comfort
4-"Forced on" geforceerd stroom gebruiken, verhoog de DHW naar 60 graden

De auto kan je laden met deze schakelaar (aan / uit;) ; switch.metered_wall_plug_switch die gebruik ongeveer 2400 Watt.

De batterij heeft twee modi; select.solarflow_2400_ac_ac_mode 
-AC Input Mode
-AC Output Mode
Met daarbij; number.solarflow_2400_ac_input_limit van 0 tot 800
En; number.solarflow_2400_ac_output_limit van 0 tot 800

Variabelen
-prijzen vooruit tibber 'sensor.tibber_prices_quarterly','tomorrow'
-verwachte opbrengst zonnepanelen sensor.energy_production_tomorrow, die kan je ook per uur krijgen
-weersverwachting, onder de 4 graden gaan we veel stroom gebruiken; weather.openweathermap) en lees forecast[1].temperature
-vacation, boolean; on = niemand thuis, niet verwarmen/coolen, geen DHW, weinig stroom gebruik


Regels
1-prijs is de hoofdmotivatie, we willen zo min mogelijk geld uitgeven, bereken de prijs goed uit. Hou daarmee rekening met de afschrijving van de batterij, het verlies bij laden en ontladen, 
2-Als er een negatieve prijs komt of komt komende 24 uur, moet er gedurende die periode zo veel mogelijk stroom gebruikt worden,
3-Naar aanloop van de negatieve prijs moet de accu gedurende de negatieve prijs zo veel mogelijk stroom gebruiken. 
4-de smart grid setting van de warmtepomp moet op ‘forced on’, zodat de boiler naar 60 graden verwarmte wordt. 
5-Hou rekening met terugleverkosten

Oplossingsrichting, wellicht niet de beste, dus staat niet vast;
Ik wil dit probleem in delen oplossen;

-Eerst wil ik een grafiek plotten voor de komende 48 uur. In deze grafiek moet per kwartier staan; 
De te verwachten opbrengst van de panelen
De prijs van de stroom
Het geschatte energie gebruik
-Ik denk dat de beste aanpak is, om bij het bekend worden van de prijzen voor komende dag, er een laadschema gemaakt moet worden voor diezelfde dag. Hier hou je natuurlijk rekening met de staat van de accu en veranderingen in gebruik. Dus deze berekening moet elk kwartier worden bijgestuurd aan de hand van de variabelen die dan beschikbaar zijn. 
-Het systeem moet gaan bijhouden hoeveel Watt acties kosten. Hoeveel Watt kost het om de boiler van 40 graden naar 60 graden te krijgen. Dit onthouden en daar in de toekomst gebruik van maken. Sla dit op in een tabel die af te lezen is in HA.








Appendix
Daikin Smart Grid operationmode;

8.2.1 "Normal operation/Free running" mode In the "Normal operation"/"Free running" operation mode, the indoor unit operates as normal, according to its owner's settings and schedules. No Smart Grid functionalities are enabled. 

8.2.2 "Recommended ON" mode In the "Recommended ON" operation mode, the Daikin Altherma system makes use of solar/grid power (when it is available, as measured by the solar inverter/energy management system) to produce domestic hot water and/or heat up or cool down the space. The amount of solar/grid power that is used for buffering depends on the domestic hot water tank and/or the room temperature. To align solar/grid capacity and the power consumption by the Daikin Altherma system, the power consumption of the indoor unit is limited either statically (by a fixed value set in the configuration web interface) or dynamically (auto-adaptively, as measured by the electricity meter – if part of the system layout). 

"6.6.5 Smart Grid capacity limitation due to buffering" [462] ▪ Restriction: Only available if a Smart Grid is installed and Recommended on mode is active. ▪ Allows you to limit the power consumption of the entire heat pump system (sum of outdoor unit and backup heater or booster heater (if electrical heaters are allowed for buffering)) with a pulse meter or by using setting [9.8.8] Limit setting kW. ▪ Limitation of power in kW.

8.2.3 "Forced OFF" mode In the "Forced OFF" operation mode, the solar inverter/energy management system trigger the system to deactivate the operation of the outdoor unit compressor and the electrical heaters. This is especially useful in case of energy management systems that react to high energy tariffs, or in case of grid overload (signaled by the energy distributor to the energy management system). Once active, "Forced OFF" mode will cause the system to stop space heating/ cooling, as well as domestic hot water production. INFORMATION Once running in one of the Smart Grid operation modes, the system will keep running in that mode until the input state of the LAN adapter is changed. Beware that if the system runs in "Forced OFF" mode for a long time, comfort issues can occur. 

8.2.4 "Forced ON" mode In the "Forced ON" operation mode, the Daikin Altherma system makes use of solar/grid power (when it is available, as measured by the solar inverter/energy management system) to produce domestic hot water and/or heat up or cool down the space. The amount of solar/grid power that is used for buffering depends on the domestic hot water tank and/or the room temperature. In contrast to the "Recommended ON" operation mode, there is NO power limitation: the system selects the comfort setpoint for space heating/cooling, and will heat up the domestic hot water tank to the maximum temperature. The outdoor unit compressor and the electrical heaters are not limited in their power consumption. 

The "Forced ON" operation mode is particularly useful in case of energy management systems that react to low energy tariffs, in case of grid overload (signaled by the energy distributor to the energy management system), or when multiple houses are connected to the grid that are controlled simultaneously, this to stabilise the grid.

