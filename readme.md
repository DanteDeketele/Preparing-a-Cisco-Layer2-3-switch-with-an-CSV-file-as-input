# Cisco switch CSV configurator

The scripts convert a semicolon-separated CSV file into Cisco IOS commands.
They support Layer 2 access ports, Layer 3 SVIs and routing, trunks, and
multiple switches.

## Install

```powershell
pip install netmiko
```

`vlan_config_converter.py` only generates configuration text. `send_config.py`
uses Netmiko to connect over SSH, send the commands, and verify the result.

## CSV format

Every file must use this header exactly:

```text
Vlan;Description;IP Address;Netmask;Switch;Ports
```

All columns are semicolon-separated. Leave a field empty when it does not
apply.

| Column | Meaning | Examples |
| --- | --- | --- |
| `Vlan` | One VLAN ID, a comma-separated list, or a range | `10`, `10,20,30`, `10-30` |
| `Description` | VLAN name, or the name written on a trunk interface | `Users`, `Uplink-to-Core` |
| `IP Address` | SVI address for a Layer 3 VLAN | `192.168.10.1` |
| `Netmask` | SVI subnet mask; required with an IP address | `255.255.255.0` |
| `Switch` | Switch ID from the multi-switch environment configuration | `1`, `2`; empty means shared |
| `Ports` | Port numbers, comma-separated ports, or port ranges | `1`, `1,2`, `1-12` |

## Row types

### Layer 2 access VLAN

No IP address or netmask. The switch ID and ports are required. The script
creates the VLAN and configures the ports as access ports.

```text
10;Users;;;1;1-12
```

Generated behavior:

```text
vlan 10
name Users
interface range Fa0/1 - 12
switchport access vlan 10
switchport mode access
```

### Layer 3 VLAN / SVI

Provide both an IP address and a netmask. The script creates the SVI and adds
`ip routing` to the switch configuration.

```text
20;Servers;192.168.20.1;255.255.255.0;1;13-16
```

The ports in this row are still configured as access ports in the VLAN. The IP
address belongs to `interface vlan 20`.

### Management SVI

Leave `Ports` empty when the SVI is a management-only interface. Set `Switch`
to the device that should receive the management SVI.

```text
99;Management;192.168.99.2;255.255.255.0;1;
```

### Default gateway

Provide an IP address without a netmask, switch, or port. This emits an
`ip default-gateway` command.

```text
;Default-gateway;192.168.99.1;;1;
```

Layer 3 switches normally use `ip routing` and static or dynamic routes. Use a
default route on a Layer 3 switch when that is what the lab topology requires.

### Trunk with a VLAN range

A row with a VLAN range or VLAN list and a port creates a trunk. The
`Description` becomes the interface description, and the VLAN value becomes
the allowed VLAN list. Do not put an IP address on a trunk row.

```text
10-30;Uplink-to-Core;;;1;24
10,20,30;Server-Trunk;;;1;23
```

The first row allows the range `10-30`; the second allows only VLANs `10`,
`20`, and `30`.

A row whose description contains `trunk` or `uplink` is also treated as a
trunk when it has a port, even if only one VLAN is listed.

### Multiple switches

Rows with a `Switch` value are sent only to that switch. Rows with an empty
`Switch` value are shared with every selected switch. For predictable lab
behavior, use explicit switch IDs as shown below.

```text
10;Users-on-Switch-1;;;1;1-12
20;Users-on-Switch-2;;;2;1-12
10-40;Trunk-on-Switch-1;;;1;23
10-40;Trunk-on-Switch-2;;;2;23
```

## Example files

- [example-L2.csv](input/example-L2.csv): access VLANs and a management SVI
- [example-L3.csv](input/example-L3.csv): several routed VLANs and management
- [example-trunk.csv](input/example-trunk.csv): VLAN range and VLAN list trunks
- [example-multi-switch.csv](input/example-multi-switch.csv): switch-specific and shared rows
- [example-default-gateway.csv](input/example-default-gateway.csv): default gateway row
- [BST-C-Core-2.csv](input/BST-C-Core-2.csv): combined two-switch lab example

## Generate configuration only

```powershell
python .\scripts\vlan_config_converter.py `
	-f .\input\example-L3.csv `
	-o .\output\example-L3.txt
```

Generate configuration for one switch from a multi-switch CSV:

```powershell
python .\scripts\vlan_config_converter.py `
	-f .\input\example-multi-switch.csv `
	-o .\output\switch-1.txt `
	--switch-id 1
```

Use `-pt Gi1/0` or another interface prefix when the switch does not use the
default `Fa0` prefix.

## Configure one switch over SSH

```powershell
python .\scripts\send_config.py `
	-f .\input\example-L2.csv `
	--host 192.168.99.10 `
	-u cisco `
	--dry-run
```

Remove `--dry-run` to connect and send the configuration. Add `-y` to skip
confirmation prompts. The script asks for a password unless `-pw` or an
environment variable supplies one.

## Configure multiple switches with `.env`

When `--host` is omitted, the script finds switch IDs in the CSV and reads
connection settings from `.env` in the project root. Use one block per switch:

```dotenv
SWITCH_1_HOST=192.168.99.11
SWITCH_1_USERNAME=cisco
SWITCH_1_PASSWORD=cisco
SWITCH_1_PORT=22
SWITCH_1_HOSTNAME=Core-1

SWITCH_2_HOST=192.168.99.12
SWITCH_2_USERNAME=cisco
SWITCH_2_PASSWORD=cisco
SWITCH_2_PORT=22
SWITCH_2_HOSTNAME=Core-2
```

Run every switch found in the CSV:

```powershell
python .\scripts\send_config.py -f .\input\example-multi-switch.csv -y
```

Run only switch 2:

```powershell
python .\scripts\send_config.py `
	-f .\input\example-multi-switch.csv `
	--switch-id 2 `
	-y
```

The same variables may be set in the process environment instead of `.env`.
Use `.env` only for this lab and do not commit real credentials.

## Validation and verification

Use `--dry-run` first. After sending, the script checks:

- `show vlan brief` for all configurations
- `show ip route` when `ip routing` was generated
- `show interfaces trunk` when a trunk was generated

## Input validation

The converter stops before writing output when it finds invalid input. Errors
include the CSV row number and the reason. It rejects missing or extra columns,
missing descriptions, invalid IPv4 addresses, incomplete IP/netmask pairs,
invalid VLAN IDs, VLAN IDs outside `1-4094`, malformed port lists, and malformed
switch assignments. This prevents a partially generated configuration from
being sent.

The SSH script also rejects ports outside `1-65535`, missing switch hosts, and
invalid per-switch environment values. Run with `--dry-run` first to review
the generated commands without opening an SSH connection.