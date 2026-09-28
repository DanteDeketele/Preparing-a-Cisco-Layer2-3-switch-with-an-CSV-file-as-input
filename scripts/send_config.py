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


# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
CONVERTER = SCRIPT_DIR / "vlan_config_converter.py"

COLOR_RED = "\033[91m"
COLOR_YELLOW = "\033[93m"
COLOR_GREEN = "\033[92m"
COLOR_BOLD = "\033[1m"
COLOR_RESET = "\033[0m"
COMMAND_TIMEOUT = 10


# -----------------------------------------------------------------------------
# Command-line arguments
# -----------------------------------------------------------------------------

def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Convert a CSV file and send the configuration to a Cisco switch."
    )
    parser.add_argument("-f", "--file", required=True, help="Input CSV file")
    parser.add_argument("--host", required=True, help="Switch management IP or hostname")
    parser.add_argument("-u", "--username", required=True, help="SSH username")
    parser.add_argument("--port", type=int, default=22, help="SSH port (default: 22)")
    parser.add_argument("-pt", "--porttype", default="Fa0", help="Interface prefix")
    parser.add_argument("-hn", "--hostname", default="Switch", help="Cisco hostname")
    parser.add_argument(
        "-pw",
        "--pw",
        dest="password",
        help="SSH password (optional; command-line passwords may be visible in shell history)",
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Automatically confirm all safety prompts",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Convert and display commands without connecting or sending them",
    )
    return parser.parse_args()


# -----------------------------------------------------------------------------
# Input validation and safety warnings
# -----------------------------------------------------------------------------

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


# -----------------------------------------------------------------------------
# Configuration conversion and temporary-file handling
# -----------------------------------------------------------------------------

def convert_csv_to_config(input_path, porttype, hostname):
    temporary_file = tempfile.NamedTemporaryFile(
        prefix="switch-config-", suffix=".txt", delete=False
    )
    output_path = Path(temporary_file.name)
    temporary_file.close()
# -----------------------------------------------------------------------------
# User-facing output and confirmation
# -----------------------------------------------------------------------------

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
        output_path.unlink(missing_ok=True)
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    if result.stdout:
        print(result.stdout, end="")
    return [
        line
        for line in output_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("!")
    ], output_path


def print_warnings(warnings, commands):
    print(f"{COLOR_RED}{COLOR_BOLD}WARNING{COLOR_RESET}")
    for warning in warnings:
        highlighted = re.sub(
            r"(management|SSH|port 24|uplink|interface)",
            rf"{COLOR_RED}{COLOR_BOLD}\1{COLOR_RESET}",
            warning,
            flags=re.IGNORECASE,
        )
        print(f"{COLOR_YELLOW}- {highlighted}{COLOR_RESET}")
    print(f"{len(commands)} commands are ready to send.")


def confirm_send(warnings, commands):
    print_warnings(warnings, commands)
    print("Review the generated commands above before continuing.")
    answer = input("Apply the configuration? [y/N]: ").strip().lower()
    if answer not in {"y", "yes"}:
        return False
    if any("contains the SSH host" in warning for warning in warnings):
        print(
            f"{COLOR_RED}{COLOR_BOLD}WARNING: This configuration may disconnect SSH.{COLOR_RESET}"
        )
        answer = input("Continue with the management change? [y/N]: ").strip().lower()
        return answer in {"y", "yes"}
    return True


# -----------------------------------------------------------------------------
# Device response and progress handling
# -----------------------------------------------------------------------------

def get_hostname(prompt):
    return prompt.strip().rstrip("#>").strip()


def find_device_errors(response):
    error_patterns = (
        r"^%\s*(?:invalid|incomplete|ambiguous|error|failed|cannot)\b",
        r"invalid input",
        r"incomplete command",
        r"ambiguous command",
        r"\berror\b",
    )
    return [
        line.strip()
        for line in response.splitlines()
        if any(re.search(pattern, line.strip(), re.IGNORECASE) for pattern in error_patterns)
    ]


def print_progress(current, total, note):
    width = 12
    completed = int(width * current / total) if total else width
    bar = "#" * completed + "." * (width - completed)
    sys.stdout.write(
        f"\r{COLOR_GREEN}[{bar}]{COLOR_RESET} {current}/{total} {note[:45]:<45}"
    )
    sys.stdout.flush()
    if current >= total:
        print()


def clear_progress():
    sys.stdout.write("\r" + (" " * 100) + "\r")
    sys.stdout.flush()


# -----------------------------------------------------------------------------
# Verification and configuration delivery
# -----------------------------------------------------------------------------

def get_verification_commands(commands):
    return ["show vlan brief"]


def verify_configuration(net_connect, commands):
    print("\nVerification output:")
    for show_command in get_verification_commands(commands):
        print(f"\n--- Checking {show_command} ---")
        print(net_connect.send_command(show_command, read_timeout=COMMAND_TIMEOUT))


def send_configuration(net_connect, commands, hostname_change):
    responses = []
    device_errors = []
    successful_commands = []
    commands_to_send = [command.strip() for command in commands if command.strip()]
    configure_response = net_connect.send_command(
        "configure terminal",
        expect_string=r"[>#]\s*$",
        read_timeout=COMMAND_TIMEOUT,
        strip_prompt=False,
        strip_command=False,
    )
    device_errors.extend(find_device_errors(configure_response))
    for command_number, command in enumerate(commands_to_send, start=1):
        prompt = net_connect.find_prompt()
        print_progress(command_number, len(commands_to_send), f"{prompt} {command}")
        try:
            response = net_connect.send_command(
                command,
                expect_string=r"[>#]\s*$",
                read_timeout=COMMAND_TIMEOUT,
                strip_prompt=False,
                strip_command=False,
            )
            responses.append(response)
            command_errors = find_device_errors(response)
            if command_errors:
                device_errors.extend(command_errors)
                clear_progress()
                print(
                    f"{COLOR_RED}{COLOR_BOLD}[device error] {prompt} {command}: "
                    f"{' | '.join(command_errors)}{COLOR_RESET}"
                )
                break
            successful_commands.append(command)
        except OSError as error:
            raise OSError(
                f"SSH channel closed on command {command_number} at {prompt}: {command}"
            ) from error
    if not hostname_change:
        responses.append(
            net_connect.send_command(
                "end",
                expect_string=r"[>#]\s*$",
                read_timeout=COMMAND_TIMEOUT,
                strip_prompt=False,
                strip_command=False,
            )
        )
    return responses, device_errors, successful_commands


# -----------------------------------------------------------------------------
# Main workflow
# -----------------------------------------------------------------------------

def main():
    arguments = parse_arguments()
    input_path = Path(arguments.file)
    if not input_path.is_file():
        raise SystemExit(f"Input CSV file not found: {input_path}")
    if not CONVERTER.is_file():
        raise SystemExit(f"Converter not found: {CONVERTER}")

    warnings = find_warnings(input_path, arguments.host)
    commands, temporary_config_path = convert_csv_to_config(
        input_path, arguments.porttype, arguments.hostname
    )
    commands = [command for command in commands if not command.startswith("hostname ")]
    print(f"[ok] Converted CSV into {len(commands)} configuration commands.")
    print(f"[ok] Configuration stored in temporary file: {temporary_config_path}")
    if arguments.dry_run:
        print_warnings(warnings, commands)
        print("Dry run complete. Nothing was sent.")
        return

    if not arguments.yes and not confirm_send(warnings, commands):
        print("Cancelled. No SSH connection was opened and nothing was sent.")
        return

    password = arguments.password or getpass.getpass("SSH password: ")
    connection = {
        "device_type": "cisco_ios",
        "host": arguments.host,
        "username": arguments.username,
        "password": password,
        "port": arguments.port,
        "keepalive": 30,
        "fast_cli": False,
        "global_delay_factor": 1,
        "read_timeout_override": COMMAND_TIMEOUT,
    }

    print(f"Connecting to {arguments.host}:{arguments.port}...")
    with ConnectHandler(**connection) as net_connect:
        prompt = net_connect.find_prompt()
        actual_hostname = get_hostname(prompt)
        print(f"[ok] Connected to switch hostname: {actual_hostname}")

        try:
            net_connect.send_command("show clock", read_timeout=COMMAND_TIMEOUT)
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
            hostname_change = arguments.yes or input(
                f"Change the hostname to '{arguments.hostname}'? [y/N]: "
            ).strip().lower() in {"yes", "y"}
            if hostname_change:
                commands.insert(0, f"hostname {arguments.hostname}")
            else:
                print("Hostname unchanged. Continuing with the VLAN configuration.")

        try:
            _, device_errors, successful_commands = send_configuration(
                net_connect, commands, hostname_change
            )
            if device_errors:
                print(
                    f"{COLOR_RED}{COLOR_BOLD}WARNING: Sending stopped after "
                    f"{len(device_errors)} device error(s).{COLOR_RESET}"
                )
            else:
                print("[ok] Configuration commands sent.")
            if hostname_change:
                print(
                    net_connect.send_command(
                        "end",
                        expect_string=r"[>#]\s*$",
                        read_timeout=COMMAND_TIMEOUT,
                    )
                )
            verify_configuration(net_connect, successful_commands)
        except OSError as error:
            print(f"WARNING: The SSH channel closed while sending configuration: {error}")
            print("Some commands may have been applied, but verification could not run.")
            print("Do not retry blindly; check the switch first.")
            return
        if device_errors:
            print("Configuration stopped. Review the switch response before disconnecting.")
        else:
            print(f"{COLOR_GREEN}{COLOR_BOLD}FINISHED{COLOR_RESET}")


if __name__ == "__main__":
    main()
