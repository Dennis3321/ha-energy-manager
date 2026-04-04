---
description: 'Describe what this custom agent does and when to use it.'
tools: []
---
Ik wil zelf software maken om mijn thuis accu te besturen volgende deze regels;
Constanten
-Type Accu Zendure SolarEdge 2400, kosten 2500 euro
-capacitiet accu; 8.64 kwh
-maximale laadsnelheid; 800 watt (firmware-update maart 2026: limiet verlaagd van 2400 naar 800W)
-maximale ontlaadsnelheid; 800 watt (firmware-update maart 2026: limiet verlaagd van 2400 naar 800W)

De batterij heeft twee modi; select.solarflow_2400_ac_ac_mode 
-AC Input Mode
-AC Output Mode
Met daarbij; number.solarflow_2400_ac_input_limit van 0 tot 800
En; number.solarflow_2400_ac_output_limit van 0 tot 800

Variabelen
-prijzen vooruit tibber 'sensor.tibber_prices_quarterly','tomorrow'
-vacation, boolean; on = niemand thuis, weinig stroom gebruik


Regels
1-prijs is de hoofdmotivatie, we willen zo min mogelijk geld uitgeven, bereken de prijs goed uit. Hou daarmee rekening met de afschrijving van de batterij, het verlies bij laden en ontladen, 
2-Als er een negatieve prijs komt of komt komende 24 uur, moet er gedurende die periode zo veel mogelijk stroom gebruikt worden,
3-Naar aanloop van de negatieve prijs moet de accu gedurende de negatieve prijs zo veel mogelijk stroom gebruiken. 
4-Hou rekening met terugleverkosten

Oplossingsrichting, wellicht niet de beste, dus staat niet vast;
Ik wil dit probleem in delen oplossen;

-Eerst wil ik een grafiek plotten voor de komende 48 uur. In deze grafiek moet per kwartier staan; 
De prijs van de stroom
-Ik denk dat de beste aanpak is, om bij het bekend worden van de prijzen voor komende dag, er een laadschema gemaakt moet worden voor diezelfde dag. Hier hou je natuurlijk rekening met de staat van de accu en veranderingen in gebruik. Dus deze berekening moet elk kwartier worden bijgestuurd aan de hand van de variabelen die dan beschikbaar zijn. 
