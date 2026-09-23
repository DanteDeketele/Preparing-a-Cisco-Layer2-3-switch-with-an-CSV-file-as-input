# Install requirements

pip install netmiko

# use

python .\test.py -f "../input/example-L2.csv" -o "../output/1.txt"

# dry run: convert and review without connecting to the switch
python .\scripts\send_config.py -f .\input\example-L2.csv --host 192.168.99.10 -u cisco --dry-run

# convert and send after typing SEND at the safety prompt
python .\scripts\send_config.py -f .\input\example-L2.csv --host 192.168.99.10 -u cisco

# automatic confirmations (use with care)
python .\scripts\send_config.py -f .\input\example-L2.csv --host 192.168.99.10 -u cisco -y

# provide the SSH password on the command line instead of being prompted
python .\scripts\send_config.py -f .\input\example-L2.csv --host 192.168.99.10 -u cisco -pw "your-password"