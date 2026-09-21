import sys 
import getopt 

def get_parameters():
    try: 
        opts, args = getopt.getopt(sys.argv[1:], "f:o:", ["file=", "output="]) 
    except getopt.GetoptError as err: 
        print(f"Error: {err}") 
        sys.exit(1) 

    for opt, arg in opts: 
        if opt in ("-f", "--file"): 
            global filename
            filename = arg
        elif opt in ("-o", "--output"): 
            global output_file
            output_file = arg
        elif opt in ("-h", "--help"):
            print("Usage: python test.py -f <input_file> -o <output_file>")
            sys.exit(0)
        elif opt in ("-hn", "--hostname"):
            global hostname
            hostname = arg

    if 'filename' not in globals() or 'output_file' not in globals():
        print("Error: Both input file and output file must be specified. Use -f <input_file> and -o <output_file>.")
        sys.exit(1)

# check if the input file exists
def check_input_file(filename):
    try:
        with open(filename, "r") as f:
            pass
    except FileNotFoundError:
        print(f"Error: Input file not found: {filename}")
        sys.exit(1)

def check_output_file(output_file):
  try:
    with open(output_file, "w") as f:
      pass
  except Exception as e:
    print(f"Error: Could not create output file: {output_file}. Error: {e}")
    sys.exit(1)

class ConfigLine:

    def __init__(self, vlan_id, vlan_name, ip_address, subnet_mask, switch, ports):
        self.vlan_id = vlan_id
        self.vlan_name = vlan_name
        self.ip_address = ip_address
        self.subnet_mask = subnet_mask
        self.switch = switch
        self.ports = ports
        self.port_ranges = []  # Initialize an empty list for port ranges
        self.port_list = []

        self.valid, self.validation_message = self.check_validity()

    def check_validity(self):
        # Check if VLAN ID is a number
        if not self.vlan_id.isdigit():
            return False, f"Invalid VLAN ID: {self.vlan_id}"

        if self.ip_address != "":  # Only check if IP address is provided
            # Check if IP address is valid
            octets = self.ip_address.split(".")
            if len(octets) != 4 or not all(o.isdigit() and 0 <= int(o) <= 255 for o in octets):
                return False, f"Invalid IP address: {self.ip_address}"

        # Check if subnet mask is valid
        if self.subnet_mask != "":
            if self.ip_address == "":
                return False, "Subnet mask provided without an IP address"
            mask_octets = self.subnet_mask.split(".")
            if len(mask_octets) != 4 or not all(m.isdigit() and 0 <= int(m) <= 255 for m in mask_octets):
                return False, f"Invalid subnet mask: {self.subnet_mask}"

        # Check if switch is not empty
        if not self.switch:
            return False, "Switch name cannot be empty"

        ports_list = self.ports.split(",")
        for port in ports_list:
            if not port.strip():
                return False, "Ports list contains empty port"
            # if a port is a range, containing a -, we remove it and add it to the port_range list
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

def get_vlan_creation_commands(config_line):
    commands = []
    commands.append(f"vlan {config_line.vlan_id}")
    commands.append(f"name {config_line.vlan_name}")
    if config_line.ip_address and config_line.subnet_mask:
        commands.append(f"interface vlan {config_line.vlan_id}")
        commands.append(f"ip address {config_line.ip_address} {config_line.subnet_mask}")
        commands.append("no shutdown")
    return commands

def main():
    get_parameters()
    check_input_file(filename)
    check_output_file(output_file)

    config_lines = []
    result = []

    result.append("! VLAN Configuration Commands")
    if 'hostname' in globals():
        result.append(f"hostname {hostname}")
    else:
        result.append("hostname Switch")

    result.append("")  # Add a blank line after the hostname

    # Read the input file and process each line
    with open(filename, "r") as f:
        lines = f.readlines()

    header = lines[0].strip().split(";")
    lines = lines[1:]  # Skip the header

    # Process each line (for demonstration, we will just print it)
    for line in lines:
        values = line.strip().split(";")
        if len(values) != len(header):
            print(f"Error: Line has incorrect number of values: {line.strip()}")
            continue

        config_line = ConfigLine(
            vlan_id=values[0],
            vlan_name=values[1],
            ip_address=values[2],
            subnet_mask=values[3],
            switch=values[4],
            ports=values[5]
        )
        if not config_line.valid:
            print(f"Error in line: {line.strip()}. Reason: {config_line.validation_message}")
            continue

        config_lines.append(config_line)


    for config in config_lines:
        print(f"VLAN ID: {config.vlan_id}, VLAN Name: {config.vlan_name}, IP Address: {config.ip_address}, Subnet Mask: {config.subnet_mask}, Switch: {config.switch}, Ports: {config.port_list}, port_ranges: {config.port_ranges}")
        commands = get_vlan_creation_commands(config)
        for cmd in commands:
            result.append(cmd)
        result.append("")  # Add a blank line between VLAN configurations

    # Write the result to the output file
    with open(output_file, "w") as f:
        for cmd in result:
            f.write(f"{cmd}\n")

main()