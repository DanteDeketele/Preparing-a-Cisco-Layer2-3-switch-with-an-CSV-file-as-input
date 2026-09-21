from netmiko import ConnectHandler

cisco = {
    'device_type': 'cisco_ios',
    'host':   '10.10.10.10',
    'username': 'test',
    'password': 'password'
}

net_connect = ConnectHandler(**cisco)

output = net_connect.send_command('show ip int brief')
print(output)