import argparse
import csv
import getpass
import ipaddress
import os
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
    parser.add_argument("--host", help="Switch management IP or hostname")
    parser.add_argument("-u", "--username", help="SSH username")
    parser.add_argument("--port", type=int, default=22, help="SSH port (default: 22)")
    parser.add_argument("-pt", "--porttype", default="Fa0", help="Interface prefix")
    parser.add_argument("-hn", "--hostname", default="Switch", help="Cisco hostname")
    parser.add_argument("--switch-id", help="Only configure this switch from the CSV")
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


def load_dotenv():
    env_path = SCRIPT_DIR.parent / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


def get_csv_switch_ids(input_path):
    switch_ids = set()
    with input_path.open(newline="", encoding="utf-8-sig") as input_file:
        for row in csv.DictReader(input_file, delimiter=";"):
            for switch_id in row.get("Switch", "").split(","):
                if switch_id.strip():
                    switch_ids.add(switch_id.strip())
    return sorted(switch_ids)


def get_connection_targets(arguments, input_path):
    if arguments.host:
        return [{
            "switch_id": arguments.switch_id,
            "host": arguments.host,
            "username": arguments.username,
            "password": arguments.password,
            "port": arguments.port,
            "hostname": arguments.hostname,
        }]

    switch_ids = [arguments.switch_id] if arguments.switch_id else get_csv_switch_ids(input_path)
    if not switch_ids:
        raise SystemExit(
            "No switch IDs found. Provide --host or add Switch values to the CSV."
        )

    targets = []
    for switch_id in switch_ids:
        prefix = f"SWITCH_{switch_id}_"
        host = os.getenv(f"{prefix}HOST")
        if not host:
            raise SystemExit(f"Missing {prefix}HOST in the environment or .env")
        targets.append({
            "switch_id": switch_id,
            "host": host,
            "username": os.getenv(f"{prefix}USERNAME", arguments.username or "cisco"),
            "password": os.getenv(f"{prefix}PASSWORD", arguments.password),
            "port": int(os.getenv(f"{prefix}PORT", arguments.port)),
            "hostname": os.getenv(f"{prefix}HOSTNAME", arguments.hostname),
        })
    return targets


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

def convert_csv_to_config(input_path, porttype, hostname, switch_id=None):
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
    if switch_id:
        command.extend(["--switch-id", switch_id])
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
    verification_commands = ["show vlan brief"]
    if any(command == "ip routing" for command in commands):
        verification_commands.append("show ip route")
    if any("switchport mode trunk" in command for command in commands):
        verification_commands.append("show interfaces trunk")
    return verification_commands


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

def configure_target(arguments, input_path, target):
    switch_id = target["switch_id"]
    commands, temporary_config_path = convert_csv_to_config(
        input_path, arguments.porttype, target["hostname"], switch_id
    )
    commands = [command for command in commands if not command.startswith("hostname ")]
    label = f"switch {switch_id}: " if switch_id else ""
    print(f"[ok] {label}converted CSV into {len(commands)} configuration commands.")
    print(f"[ok] {label}configuration stored in temporary file: {temporary_config_path}")
    warnings = find_warnings(input_path, target["host"])
    if arguments.dry_run:
        print_warnings(warnings, commands)
        target_name = label.rstrip(": ") or "switch"
        print(f"Dry run complete for {target_name}. Nothing was sent.")
        return

    if not arguments.yes and not confirm_send(warnings, commands):
        target_name = label.rstrip(": ") or "switch"
        print(f"Cancelled for {target_name}. No configuration was sent.")
        return

    password = target["password"] or getpass.getpass(
        f"SSH password for {label.rstrip(':') or target['host']}: "
    )
    connection = {
        "device_type": "cisco_ios",
        "host": target["host"],
        "username": target["username"],
        "password": password,
        "port": target["port"],
        "keepalive": 30,
        "fast_cli": False,
        "global_delay_factor": 1,
        "read_timeout_override": COMMAND_TIMEOUT,
    }

    print(f"Connecting to {target['host']}:{target['port']} ({label.rstrip(':') or 'switch'})...")
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
        if actual_hostname.lower() != target["hostname"].lower():
            print(
                "WARNING: The connected hostname differs from the requested hostname: "
                f"'{actual_hostname}' versus '{target['hostname']}'."
            )
            hostname_change = arguments.yes or input(
                f"Change the hostname to '{target['hostname']}'? [y/N]: "
            ).strip().lower() in {"yes", "y"}
            if hostname_change:
                commands.insert(0, f"hostname {target['hostname']}")
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


def main():
    arguments = parse_arguments()
    load_dotenv()
    input_path = Path(arguments.file)
    if not input_path.is_file():
        raise SystemExit(f"Input CSV file not found: {input_path}")
    if not CONVERTER.is_file():
        raise SystemExit(f"Converter not found: {CONVERTER}")
    if arguments.host and not arguments.username:
        raise SystemExit("--username is required when --host is provided")

    for target in get_connection_targets(arguments, input_path):
        configure_target(arguments, input_path, target)


if __name__ == "__main__":
    main()
