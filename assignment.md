Maak een python script die rechtstreeks via SSH een Cisco switch programmeert als layer-2 of layer-3 switch.
Maak gebruik van de netmiko library (https://github.com/ktbyers/netmikoLinks to an external site. / https://github.com/ktbyers/netmiko/blob/develop/EXAMPLES.mdLinks to an external site.).

De CSV header ziet er als volgt uit: vlan, description, ip address, subnetmask, switch, ports.

    Layer-3 VLAN ? => Alle velden zijn ingevuld.
    Layer-2 VLAN ? => Geen IP gegevens ingevuld.
    Mgt VLAN? => Meestal geen poort ingevuld.
        leidt de default gateway af van de ip gegevens van de management vlan
    Trunk ? => Description bevat 'trunk' of 'uplink'. Indien vlans zijn ingevuld, is er VLAN filtering.
    Indien alle vlans IP gegevens bevatten, routing activeren op de layer-3 switch.

Start op het einde je TFTP-server op en download de config.

Probeer de verschillende scenario's uit !!!