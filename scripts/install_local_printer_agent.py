#!/usr/bin/env python3
"""Install the local scanner helper so it starts at login.

The admin page discovers printers through this helper. A browser cannot browse
Bonjour itself, and the website server is not on the shop network. Run this
once on the computer that uses the admin page:

    python3 scripts/install_local_printer_agent.py

The helper listens only on 127.0.0.1. Use --uninstall to remove the login item.
"""
import argparse
import os
import plistlib
import subprocess
import sys
from pathlib import Path

LABEL = 'com.marshallauto.local-printer-agent'
ROOT = Path(__file__).resolve().parents[1]
AGENT_SCRIPT = ROOT / 'scripts' / 'local_printer_agent.py'


def launch_agent_path(home):
    return Path(home) / 'Library' / 'LaunchAgents' / f'{LABEL}.plist'


def log_path(home):
    return Path(home) / 'Library' / 'Logs' / 'marshallauto-local-printer-agent.log'


def launch_agent_definition(python_path, script_path, working_directory, log_file):
    """Return a per-user login agent that starts the loopback scanner helper."""
    return {
        'Label': LABEL,
        'ProgramArguments': [str(python_path), str(script_path)],
        'WorkingDirectory': str(working_directory),
        'RunAtLoad': True,
        'KeepAlive': True,
        'ThrottleInterval': 10,
        'ProcessType': 'Interactive',
        'LimitLoadToSessionType': ['Aqua', 'Background'],
        'StandardOutPath': str(log_file),
        'StandardErrorPath': str(log_file),
        'EnvironmentVariables': {
            'PATH': '/usr/bin:/bin:/usr/sbin:/sbin',
        },
    }


def write_launch_agent(home, python_path):
    path = launch_agent_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path(home)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    definition = launch_agent_definition(python_path, AGENT_SCRIPT, ROOT, log_file)
    with path.open('wb') as handle:
        plistlib.dump(definition, handle)
    return path


def _gui_domain():
    return f'gui/{os.getuid()}'


def _launchctl(args):
    return subprocess.run(
        ['launchctl', *args], check=False, capture_output=True, text=True,
    )


def load_launch_agent(path):
    domain = _gui_domain()
    _launchctl(['bootout', domain, str(path)])
    result = _launchctl(['bootstrap', domain, str(path)])
    if result.returncode:
        detail = (result.stderr or result.stdout or 'launchctl bootstrap failed').strip()
        raise RuntimeError(detail)
    _launchctl(['enable', f'{domain}/{LABEL}'])


def uninstall_launch_agent(home):
    path = launch_agent_path(home)
    if path.is_file():
        _launchctl(['bootout', _gui_domain(), str(path)])
        path.unlink()
    return path


def main():
    parser = argparse.ArgumentParser(description='Install the local scanner helper at login.')
    parser.add_argument('--uninstall', action='store_true', help='Remove the login item.')
    args = parser.parse_args()
    home = Path.home()
    if sys.platform != 'darwin':
        raise SystemExit('This installer supports macOS login items. On this computer, start scripts/local_printer_agent.py in the background instead.')
    if args.uninstall:
        uninstall_launch_agent(home)
        print(f'Removed {LABEL}.')
        return
    if not AGENT_SCRIPT.is_file():
        raise SystemExit(f'Missing helper script: {AGENT_SCRIPT}')
    path = write_launch_agent(home, Path(sys.executable))
    load_launch_agent(path)
    print(f'{LABEL} will start at login and is running now.')
    print('It listens only on 127.0.0.1, starting at port 8765.')
    print(f'Log: {log_path(home)}')


if __name__ == '__main__':
    main()
