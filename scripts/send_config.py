import argparse
import csv
import getpass
import ipaddress
import re
import subprocess
import sys
import tempfile
import socket
from pathlib import Path

from netmiko import ConnectHandler


SCRIPT_DIR = Path(__file__).resolve().parent
CONVERTER = SCRIPT_DIR / "test.py"

COLOR_RED = "\033[91m"
COLOR_YELLOW = "\033[93m"
COLOR_BOLD = "\033[1m"
COLOR_RESET = "\033[0m"


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Convert a CSV file with test.py and send the configuration to a Cisco switch."
    )
    parser.add_argument("-f", "--file", required=True, help="Input CSV file")
    parser.add_argument("--host", required=True, help="Switch management IP or hostname")
    parser.add_argument("-u", "--username", required=True, help="SSH username")
    parser.add_argument("--port", type=int, default=22, help="SSH port (default: 22)")
    parser.add_argument("-pt", "--porttype", default="Gi0", help="Interface prefix")
    parser.add_argument("-hn", "--hostname", default="Switch", help="Cisco hostname")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Convert and display commands without connecting or sending them",
    )
    return parser.parse_args()


def contains_port_24(value):
    for part in value.split(","):
        part = part.strip()
        if part == "24":
            return True
        range_match = re.fullmatch(r"(\d+)-(\d+)", part)
        if range_match:
            start, end = map(int, range_match.groups())
            if min(start, end) <= 24 <= max(start, end):
                return True
    return False


def management_network_contains_host(input_path, host):
    try:
        host_address = ipaddress.ip_address(socket.gethostbyname(host))
    except (OSError, ValueError):
        return False

    with input_path.open(newline="", encoding="utf-8-sig") as input_file:
        for row in csv.DictReader(input_file, delimiter=";"):
            if "management" not in row.get("Description", "").lower():
                continue
            management_ip = row.get("IP Address", "").strip()
            subnet_mask = row.get("Netmask", "").strip()
            if not management_ip or not subnet_mask:
                continue
            try:
                network = ipaddress.ip_network(
                    f"{management_ip}/{subnet_mask}", strict=False
                )
            except ValueError:
                continue
            return host_address in network
    return False


def find_warnings(input_path, host):
    content = input_path.read_text(encoding="utf-8-sig")
    warnings = [
        "Do not change or shut down the interface carrying this SSH session.",
    ]
    if re.search(r"management", content, re.IGNORECASE):
        if management_network_contains_host(input_path, host):
            warnings.append(
                "The CSV mentions management and its subnet contains the SSH host."
            )
        else:
            warnings.append(
                "The CSV mentions management, but its subnet appears separate from the SSH host."
            )
    if any(contains_port_24(line.split(";")[-1]) for line in content.splitlines()[1:]):
        warnings.append(
            "Port 24 is mentioned. This is commonly used for the SSH/uplink connection."
        )
    return warnings


def convert_with_test(input_path, porttype, hostname):
    with tempfile.TemporaryDirectory() as temporary_directory:
        output_path = Path(temporary_directory) / "converted.txt"
        command = [
            sys.executable,
            str(CONVERTER),
            "--file",
            str(input_path),
            "--output",
            str(output_path),
            "--porttype",
            porttype,
            "--hostname",
            hostname,
        ]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip())
        if result.stdout:
            print(result.stdout, end="")
        return [
            line
            for line in output_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("!")
        ]


def print_warnings(warnings, commands):
    print(f"\n{COLOR_RED}{COLOR_BOLD}WARNING{COLOR_RESET}")
    for warning in warnings:
        highlighted = re.sub(
            r"(management|SSH|port 24|uplink|interface)",
            rf"{COLOR_RED}{COLOR_BOLD}\1{COLOR_RESET}",
            warning,
            flags=re.IGNORECASE,
        )
        print(f"{COLOR_YELLOW}- {highlighted}{COLOR_RESET}")
    print(f"\n{len(commands)} commands are ready to send.")


def confirm_send(warnings, commands):
    print_warnings(warnings, commands)
    print("Review the generated commands above before continuing.")
    if input("Type SEND to connect and apply the configuration: ").strip() != "SEND":
        return False
    if any("contains the SSH host" in warning for warning in warnings):
        print(
            f"{COLOR_RED}{COLOR_BOLD}WARNING: This configuration may disconnect SSH.{COLOR_RESET}"
        )
        return (
            input("Type CHANGE-MANAGEMENT to continue anyway: ").strip()
            == "CHANGE-MANAGEMENT"
        )
    return True


def get_hostname(prompt):
    return prompt.strip().rstrip("#>").strip()


def print_progress(current, total, note):
    width = 24
    completed = int(width * current / total) if total else width
    bar = "#" * completed + "." * (width - completed)
    sys.stdout.write(f"\r[{bar}] {current}/{total} {note[:55]:<55}")
    sys.stdout.flush()
    if current >= total:
        print()


def get_verification_commands(commands):
    verification_commands = ["show vlan brief"]
    for command in commands:
        match = re.fullmatch(r"interface vlan (\d+)", command, re.IGNORECASE)
        if match:
            verification_commands.append(f"show running-config interface Vlan{match.group(1)}")
    return verification_commands


def verify_configuration(net_connect, commands):
    print("\nVerification output:")
    show_commands = get_verification_commands(commands)
    for command_number, show_command in enumerate(show_commands, start=1):
        print_progress(command_number, len(show_commands), f"Running {show_command}")
        print(f"\n--- {show_command} ---")
        print(net_connect.send_command(show_command, read_timeout=30))


def send_configuration(net_connect, commands, hostname_change):
    responses = []
    commands_to_send = [command.strip() for command in commands if command.strip()]
    net_connect.send_command_timing("configure terminal", read_timeout=30)
    for command_number, command in enumerate(commands_to_send, start=1):
        print_progress(command_number, len(commands_to_send), f"Sending {command}")
        try:
            responses.append(
                net_connect.send_command_timing(
                    command,
                    read_timeout=30,
                    strip_prompt=False,
                    strip_command=False,
                )
            )
        except OSError as error:
            raise OSError(
                f"SSH channel closed on command {command_number}: {command}"
            ) from error
    if not hostname_change:
        responses.append(net_connect.send_command_timing("end", read_timeout=30))
    return responses


def main():
    arguments = parse_arguments()
    input_path = Path(arguments.file)
    if not input_path.is_file():
        raise SystemExit(f"Input CSV file not found: {input_path}")
    if not CONVERTER.is_file():
        raise SystemExit(f"Converter not found: {CONVERTER}")

    warnings = find_warnings(input_path, arguments.host)
    commands = convert_with_test(input_path, arguments.porttype, arguments.hostname)
    commands = [command for command in commands if not command.startswith("hostname ")]
    print(f"[ok] Converted CSV into {len(commands)} configuration commands.")

    print("\nGenerated configuration:")
    print("\n".join(commands))
    if arguments.dry_run:
        print_warnings(warnings, commands)
        print("\nDry run complete. Nothing was sent.")
        return

    if not confirm_send(warnings, commands):
        print("Cancelled. No SSH connection was opened and nothing was sent.")
        return

    password = getpass.getpass("SSH password: ")
    connection = {
        "device_type": "cisco_ios",
        "host": arguments.host,
        "username": arguments.username,
        "password": password,
        "port": arguments.port,
        "keepalive": 30,
        "fast_cli": False,
        "global_delay_factor": 2,
    }

    print(f"Connecting to {arguments.host}:{arguments.port}...")
    with ConnectHandler(**connection) as net_connect:
        prompt = net_connect.find_prompt()
        actual_hostname = get_hostname(prompt)
        print(f"[ok] Connected to switch hostname: {actual_hostname}")

        try:
            net_connect.send_command("show clock", read_timeout=20)
            print("[ok] SSH channel preflight passed.")
        except OSError as error:
            print(f"WARNING: The SSH channel closed before configuration was sent: {error}")
            print("No configuration was sent. Check the switch SSH/session settings and try again.")
            return

        hostname_change = False
        if actual_hostname.lower() != arguments.hostname.lower():
            print(
                "WARNING: The connected hostname differs from the requested hostname: "
                f"'{actual_hostname}' versus '{arguments.hostname}'."
            )
            hostname_change = input(
                f"Change the hostname to '{arguments.hostname}'? (yes/no): "
            ).strip().lower() in {"yes", "y"}
            if hostname_change:
                commands.insert(0, f"hostname {arguments.hostname}")
            else:
                print("Hostname unchanged. Continuing with the VLAN configuration.")

        try:
            send_configuration(net_connect, commands, hostname_change)
            print("[ok] Configuration commands sent.")
            if hostname_change:
                print(net_connect.send_command_timing("end", read_timeout=30))
            verify_configuration(net_connect, commands)
        except OSError as error:
            print(f"WARNING: The SSH channel closed while sending configuration: {error}")
            print("Some commands may have been applied, but verification could not run.")
            print("Do not retry blindly; check the switch first.")
            return
        print("Configuration sent. Review the switch response before disconnecting.")


if __name__ == "__main__":
    main()
