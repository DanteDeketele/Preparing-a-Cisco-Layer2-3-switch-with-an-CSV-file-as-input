# Install requirements

pip install netmiko

# use

python .\scripts\vlan_config_converter.py -f ".\input\example-L2.csv" -o ".\output\1.txt"

# dry run: convert and review without connecting to the switch
python .\scripts\send_config.py -f .\input\example-L2.csv --host 192.168.99.10 -u cisco --dry-run

# convert and send after typing SEND at the safety prompt
python .\scripts\send_config.py -f .\input\example-L2.csv --host 192.168.99.10 -u cisco

# automatic confirmations (use with care)
python .\scripts\send_config.py -f .\input\example-L2.csv --host 192.168.99.10 -u cisco -y

# provide the SSH password on the command line instead of being prompted
python .\scripts\send_config.py -f .\input\example-L2.csv --host 192.168.99.10 -u cisco -pw "your-password"

# Layer 3 and trunk CSV rows

# A VLAN row with IP Address and Netmask creates an SVI. If at least one SVI is
# present, the converter also enables `ip routing`.
# A row with multiple VLAN IDs or a VLAN range and a port creates a trunk. The
# Description becomes the interface description and the VLAN field is used for
# `switchport trunk allowed vlan`.
# Example:
# 1971-1972;Trunk;;;;23

# Multiple switches

# When --host is omitted, the script finds switch IDs in the CSV and loads each
# device from .env (or the process environment):
# SWITCH_1_HOST=192.168.99.11
# SWITCH_1_USERNAME=cisco
# SWITCH_1_PASSWORD=cisco
# SWITCH_1_PORT=22
# SWITCH_1_HOSTNAME=Core-1
# SWITCH_2_HOST=192.168.99.12
# SWITCH_2_USERNAME=cisco
# SWITCH_2_PASSWORD=cisco
# SWITCH_2_PORT=22
# SWITCH_2_HOSTNAME=Core-2
#
# Run all switches:
# python .\scripts\send_config.py -f .\input\BST-C-Core-2.csv -y
#
# Run only one switch:
# python .\scripts\send_config.py -f .\input\BST-C-Core-2.csv --switch-id 2 -y
#
# Rows with a Switch value are sent only to that switch. Rows with an empty
# Switch value are shared with every selected switch, such as the trunk row.