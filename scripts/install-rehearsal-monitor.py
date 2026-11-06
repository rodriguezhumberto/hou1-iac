#!/usr/bin/env python3
"""Install the test monitor as controller user services; use --enable after drills."""

import argparse
import json
from pathlib import Path
import shutil
import subprocess

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--config", required=True)
p.add_argument("--enable", action="store_true")
a = p.parse_args()
config = Path(a.config).resolve()
assert json.loads(config.read_text())["environment"] == "test"
root = config.parent
assert root.stat().st_mode & 0o077 == 0
program = root / "monitor.py"
shutil.copyfile(Path(__file__).with_name("rehearsal-monitor.py"), program)
program.chmod(0o700)
tls_program = root / "controller-tls.py"
shutil.copyfile(Path(__file__).with_name("rehearsal-controller-tls.py"), tls_program)
tls_program.chmod(0o700)
units = Path.home() / ".config/systemd/user"
units.mkdir(parents=True, exist_ok=True)
files = {
    "hou1-test-alert-receiver.service": f"""[Unit]
Description=Private isolated HOU1 rehearsal alert receipt
[Service]
ExecStart=/usr/bin/python3 {program} --config {config} --receive
Restart=on-failure
UMask=0077
NoNewPrivileges=yes
[Install]
WantedBy=default.target
""",
    "hou1-test-monitor.service": f"""[Unit]
Description=Off-host HOU1 isolated test checks
After=hou1-test-alert-receiver.service
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 {program} --config {config}
TimeoutStartSec=240
UMask=0077
NoNewPrivileges=yes
""",
    "hou1-test-monitor.timer": """[Unit]
Description=Check isolated HOU1 stack every minute after completion
[Timer]
OnBootSec=2min
OnUnitInactiveSec=60s
Unit=hou1-test-monitor.service
[Install]
WantedBy=timers.target
""",
}
files["hou1-test-tls.service"] = f"""[Unit]
Description=Controller-only isolated test certificate renewal
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 {tls_program} --config {config} --renew
TimeoutStartSec=900
UMask=0077
NoNewPrivileges=yes
"""
files["hou1-test-tls.timer"] = """[Unit]
Description=Renew isolated test TLS twice daily without DNS credentials on test
[Timer]
OnCalendar=*-*-* 03,15:15:00 UTC
Persistent=true
Unit=hou1-test-tls.service
[Install]
WantedBy=timers.target
"""
for name, text in files.items():
    (units / name).write_text(text)
subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
subprocess.run(
    ["systemctl", "--user", "enable", "--now", "hou1-test-alert-receiver.service"],
    check=True,
)
if a.enable:
    subprocess.run(
        [
            "systemctl",
            "--user",
            "enable",
            "--now",
            "hou1-test-monitor.timer",
            "hou1-test-tls.timer",
        ],
        check=True,
    )
print(
    "private test receiver installed; monitor timer "
    + ("enabled" if a.enable else "awaits completed drills")
)
