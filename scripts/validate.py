"""Offline source syntax checks; dependencies are installed by `make setup`."""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*args):
    print("+", " ".join(map(str, args)), flush=True)
    subprocess.run(args, cwd=ROOT, check=True)


def main():
    # Include new source files, exclude venvs and ignored/generated artifacts.
    sources = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z",
         "--", "*.py", "*.sh"], cwd=ROOT,
    ).decode().split("\0")
    for name in filter(None, sources):
        path = ROOT / name
        if path.suffix == ".py":
            # Parse without executing host/controller code or writing bytecode.
            compile(path.read_bytes(), name, "exec")
        else:
            run("bash", "-n", name)
    print("Python and shell syntax passed.", flush=True)

    for playbook in sorted((ROOT / "ansible/playbooks").glob("*.yml")):
        run("ansible-playbook", "-i", "ansible/inventory/inventory.yml",
            str(playbook.relative_to(ROOT)), "--syntax-check")


if __name__ == "__main__":
    # Use the selected Python environment's Ansible and a disposable config,
    # independent of private controller settings and secret-decryption plugins.
    os.environ["PATH"] = str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"]
    os.environ.pop("ANSIBLE_VARS_ENABLED", None)
    with tempfile.TemporaryDirectory(prefix="hou1-validation-") as directory:
        config = Path(directory) / "ansible.cfg"
        config.write_text("[defaults]\nvars_plugins_enabled = host_group_vars\n")
        os.environ["ANSIBLE_CONFIG"] = str(config)
        if sys.argv[1:2] == ["test"]:
            run(sys.executable, "-m", "unittest", *sys.argv[2:])
        elif len(sys.argv) == 1:
            main()
        else:
            sys.exit("Usage: validate.py [test <unittest arguments>]")
