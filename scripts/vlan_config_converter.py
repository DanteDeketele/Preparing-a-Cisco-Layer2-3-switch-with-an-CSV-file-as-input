import argparse
import csv
import ipaddress
from pathlib import Path

COMMAND_SWITCHPORT_MODE_ACCESS = "switchport mode access"
COMMAND_SWITCHPORT_ACCESS_VLAN = "switchport access vlan"
COMMAND_SWITCHPORT_MODE_TRUNK = "switchport mode trunk"
COMMAND_SWITCHPORT_TRUNK_ALLOWED_VLAN = "switchport trunk allowed vlan"
COMMAND_SPANNING_TREE_PORTFAST = "spanning-tree portfast"
COMMAND_NO_SHUTDOWN = "no shutdown"
COMMAND_INTERFACE_VLAN = "interface vlan"
COMMAND_IP_ADDRESS = "ip address"
COMMAND_NO_IP_ADDRESS = "no ip address"
EXTENDED_VLAN_MIN = 1006

def get_parameters():
    parser = argparse.ArgumentParser(description="Convert a Cisco VLAN CSV file to configuration text.")
    parser.add_argument("-f", "--file", required=True, help="Input CSV file")
    parser.add_argument("-o", "--output", required=True, help="Output text file")
    parser.add_argument("-hn", "--hostname", default="Switch", help="Switch hostname")
    parser.add_argument("-pt", "--porttype", default="Fa0", help="Interface prefix")
    parser.add_argument("--switch-id", help="Only convert rows assigned to this switch")
    return parser.parse_args()

class ConfigLine:

    def __init__(self, vlan_id, vlan_name, ip_address, subnet_mask, switch, ports, porttype):
        self.vlan_id = vlan_id.strip()
        self.vlan_name = vlan_name.strip()
        self.ip_address = ip_address.strip()
        self.subnet_mask = subnet_mask.strip()
        self.switch = switch.strip()
        self.ports = ports.strip()
        self.porttype = porttype
        self.port_ranges = []
        self.port_list = []
        self.vlan_list = self.parse_vlan_list()
        self.is_trunk = bool(
            self.ports
            and (len(self.vlan_list) > 1 or any(
                separator in self.vlan_id for separator in (",", "-")
            ) or any(
                keyword in self.vlan_name.lower() for keyword in ("trunk", "uplink")
            ))
        )
        self.is_management = bool(
            self.ip_address and self.subnet_mask and not self.switch and not self.ports
        )
        self.is_default_gateway = bool(
            self.ip_address
            and not self.vlan_id
            and not self.subnet_mask
            and not self.ports
        )

        self.valid, self.validation_message = self.check_validity()

    

    def check_validity(self):
        if not self.is_default_gateway and not self.vlan_list:
            return False, f"Invalid VLAN ID: {self.vlan_id}"

        if self.is_trunk and (self.ip_address or self.subnet_mask):
            return False, "Trunk rows cannot contain an IP address or subnet mask"

        if self.ip_address:
            try:
                ipaddress.ip_address(self.ip_address)
            except ValueError:
                return False, f"Invalid IP address: {self.ip_address}"

        if self.subnet_mask:
            if self.ip_address == "":
                return False, "Subnet mask provided without an IP address"
            try:
                ipaddress.IPv4Network(f"0.0.0.0/{self.subnet_mask}")
            except ValueError:
                return False, f"Invalid subnet mask: {self.subnet_mask}"

        if (
            not self.switch
            and not self.is_management
            and not self.is_default_gateway
            and not self.is_trunk
        ):
            return False, "Switch name cannot be empty"

        if not self.ports:
            if self.is_default_gateway:
                return True, "Valid default gateway configuration line"
            return True, "Valid management configuration line" if self.is_management else "Valid configuration line"

        ports_list = self.ports.split(",")
        for port in ports_list:
            if not port.strip():
                return False, "Ports list contains empty port"
            if "-" in port:
                port_range = port.strip().split("-")
                if len(port_range) != 2 or not all(p.isalnum() for p in port_range):
                    return False, f"Invalid port range: {port.strip()}"
                else:
                    self.port_ranges.append(port)

            elif not port.strip().isalnum():
                return False, f"Invalid port name: {port.strip()}"

            else:
                self.port_list.append(port.strip())

        
        return True, "Valid configuration line"

    def parse_vlan_list(self):
        if not self.vlan_id:
            return []

        vlan_ids = []
        for item in self.vlan_id.split(","):
            item = item.strip()
            if not item:
                return []
            if "-" in item:
                bounds = item.split("-")
                if len(bounds) != 2 or not all(bound.isdigit() for bound in bounds):
                    return []
                start, end = map(int, bounds)
                if start > end:
                    return []
                vlan_ids.extend(range(start, end + 1))
            elif item.isdigit():
                vlan_ids.append(int(item))
            else:
                return []
        return vlan_ids

def get_vlan_creation_commands(config_line):
    commands = []
    if config_line.is_trunk:
        return commands
    commands.append(f"vlan {config_line.vlan_id}")
    commands.append(f"name {config_line.vlan_name}")
    if config_line.ip_address and config_line.subnet_mask:
        commands.append(f"{COMMAND_INTERFACE_VLAN} {config_line.vlan_id}")
        commands.append(f"{COMMAND_IP_ADDRESS} {config_line.ip_address} {config_line.subnet_mask}")
        commands.append(COMMAND_NO_SHUTDOWN)
        commands.append("exit")
    else:
        commands.append(f"{COMMAND_INTERFACE_VLAN} {config_line.vlan_id}")
        commands.append(COMMAND_NO_IP_ADDRESS)
        commands.append(COMMAND_NO_SHUTDOWN)
        commands.append("exit")
    return commands

def has_extended_vlan(config_lines):
    return any(
        not config_line.is_default_gateway
        and any(vlan_id >= EXTENDED_VLAN_MIN for vlan_id in config_line.vlan_list)
        for config_line in config_lines
    )

def has_layer3_vlans(config_lines):
    return any(
        config_line.ip_address and config_line.subnet_mask
        for config_line in config_lines
    )

def get_port_configuration_commands(config_line):
    commands = []
    vlan_list = config_line.vlan_id.replace(" ", "")
    if config_line.is_trunk:
        for port in config_line.port_list:
            commands.append(f"interface {config_line.porttype}/{port}")
            commands.append(f"\tdescription {config_line.vlan_name}")
            commands.append(f"\t{COMMAND_SWITCHPORT_MODE_TRUNK}")
            commands.append(f"\t{COMMAND_SWITCHPORT_TRUNK_ALLOWED_VLAN} {vlan_list}")
            commands.append(f"\t{COMMAND_NO_SHUTDOWN}")
            commands.append(f"\texit")
        for port_range in config_line.port_ranges:
            start_port, end_port = port_range.split("-")
            commands.append(
                f"interface range {config_line.porttype}/{start_port} - {end_port}"
            )
            commands.append(f"\tdescription {config_line.vlan_name}")
            commands.append(f"\t{COMMAND_SWITCHPORT_MODE_TRUNK}")
            commands.append(f"\t{COMMAND_SWITCHPORT_TRUNK_ALLOWED_VLAN} {vlan_list}")
            commands.append(f"\t{COMMAND_NO_SHUTDOWN}")
            commands.append(f"\texit")
        return commands

    for port in config_line.port_list:
        commands.append(f"interface {config_line.porttype}/{port}")
        commands.append(f"\t{COMMAND_SWITCHPORT_ACCESS_VLAN} {config_line.vlan_id}")
        commands.append(f"\t{COMMAND_SWITCHPORT_MODE_ACCESS}")
        commands.append(f"\t{COMMAND_SPANNING_TREE_PORTFAST}")
        commands.append(f"\t{COMMAND_NO_SHUTDOWN}")
        commands.append(f"\texit")
    for port_range in config_line.port_ranges:
        start_port, end_port = port_range.split("-")
        commands.append(
            f"interface range {config_line.porttype}/{start_port} - {end_port}"
        )
        commands.append(f"\t{COMMAND_SWITCHPORT_ACCESS_VLAN} {config_line.vlan_id}")
        commands.append(f"\t{COMMAND_SWITCHPORT_MODE_ACCESS}")
        commands.append(f"\t{COMMAND_SPANNING_TREE_PORTFAST}")
        commands.append(f"\t{COMMAND_NO_SHUTDOWN}")
        commands.append(f"\texit")
    return commands

def main():
    arguments = get_parameters()
    input_path = Path(arguments.file)
    output_path = Path(arguments.output)
    if not input_path.is_file():
        raise SystemExit(f"Error: Input file not found: {input_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    config_lines = []
    result = []

    result.append("! VLAN Configuration Commands")
    result.append(f"hostname {arguments.hostname}")

    result.append("")  # Add a blank line after the hostname

    with input_path.open(newline="", encoding="utf-8-sig") as input_file:
        rows = csv.DictReader(input_file, delimiter=";")
        required_columns = {"Vlan", "Description", "IP Address", "Netmask", "Switch", "Ports"}
        if not rows.fieldnames or not required_columns.issubset(rows.fieldnames):
            missing = ", ".join(sorted(required_columns - set(rows.fieldnames or [])))
            raise SystemExit(f"Error: CSV is missing these columns: {missing}")

        for row_number, row in enumerate(rows, start=2):
            row_switch = row["Switch"].strip()
            if arguments.switch_id and row_switch and arguments.switch_id not in {
                switch_id.strip() for switch_id in row_switch.split(",")
            }:
                continue
            config_line = ConfigLine(
                row["Vlan"], row["Description"], row["IP Address"],
                row["Netmask"], row["Switch"], row["Ports"], arguments.porttype
            )
            if not config_line.valid:
                print(f"Error on CSV row {row_number}: {config_line.validation_message}")
                continue
            config_lines.append(config_line)


    if has_extended_vlan(config_lines):
        result.append("vtp mode transparent")
        result.append("")

    if has_layer3_vlans(config_lines):
        result.append("ip routing")
        result.append("")

    for config in config_lines:
        if config.is_default_gateway:
            result.append(f"ip default-gateway {config.ip_address}")
            result.append("")
            continue
        commands = get_vlan_creation_commands(config)
        if config.ports:
            commands.append("")
            commands += get_port_configuration_commands(config)
        for cmd in commands:
            result.append(cmd)
        result.append("")  # Add a blank line between VLAN configurations

    with output_path.open("w", encoding="utf-8") as f:
        for cmd in result:
            f.write(f"{cmd}\n")

if __name__ == "__main__":
    main()