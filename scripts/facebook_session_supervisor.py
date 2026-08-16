"""Supervise the operator desktop; fail/restart if any component exits."""
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import time


def connectable(address, family):
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        try:
            sock.connect(address)
            return True
        except OSError:
            return False


def wait_ready(process, address, family, name):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f'{name} exited; see /tmp/{name}.log')
        if connectable(address, family):
            return
        time.sleep(0.2)
    raise RuntimeError(f'{name} did not become ready; see /tmp/{name}.log')


def main():
    display = os.environ.setdefault('DISPLAY', ':99')
    if not re.fullmatch(r':\d+', display):
        raise RuntimeError('Session manager requires a local DISPLAY such as :99')
    display_number = display[1:]
    x_socket = f'/tmp/.X11-unix/X{display_number}'
    if connectable(x_socket, socket.AF_UNIX):
        raise RuntimeError(f'Display {display} is already in use')
    # Containers retain /tmp on restart. Remove only this inactive display's locks.
    Path(f'/tmp/.X{display_number}-lock').unlink(missing_ok=True)
    Path(x_socket).unlink(missing_ok=True)
    children = []
    stopping = False

    def stop(signum, frame):
        nonlocal stopping
