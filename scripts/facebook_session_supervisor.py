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
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    def start(name, args, log=True):
        if log:
            with open(f'/tmp/{name}.log', 'w') as output:
                child = subprocess.Popen(args, stdout=output, stderr=subprocess.STDOUT)
        else:
            child = subprocess.Popen(args)
        children.append(child)
        return child

    try:
        xvfb = start('xvfb', ['Xvfb', display, '-screen', '0', os.environ.get('FACEBOOK_SESSION_SCREEN_RESOLUTION', '1366x900x24'), '-nolisten', 'tcp'])
        wait_ready(xvfb, x_socket, socket.AF_UNIX, 'xvfb')
        start('fluxbox', ['fluxbox'])
        vnc = start('x11vnc', ['x11vnc', '-display', display, '-forever', '-shared', '-nopw', '-listen', '127.0.0.1', '-rfbport', '5900'])
        wait_ready(vnc, ('127.0.0.1', 5900), socket.AF_INET, 'x11vnc')
        proxy = start('novnc', ['websockify', '--web=/usr/share/novnc', '0.0.0.0:7900', '127.0.0.1:5900'])
        wait_ready(proxy, ('127.0.0.1', 7900), socket.AF_INET, 'novnc')
        manager = start('session-manager', [sys.executable, 'manage.py', 'run_facebook_session_manager', *sys.argv[1:]], log=False)
        while not stopping:
            for child in children:
                code = child.poll()
                if code is not None:
                    if child is manager and code == 0:
                        return
                    raise RuntimeError(f'Session component exited: {child.args[0]} ({code})')
            time.sleep(0.5)
    finally:
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
        for child in reversed(children):
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


if __name__ == '__main__':
    main()
