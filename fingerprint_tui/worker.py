"""Short-lived authorized fprintd client; presentation stays unprivileged."""
import json
import os
import pwd
import sys

from .backend import Device, Error, connect, devices, error_text
import dbus


def main():
    def emit(event):
        print(json.dumps(event), flush=True)

    try:
        if os.geteuid() != 0 or len(sys.argv) != 5:
            raise Error('Start fingerprint-tui as your normal user.')
        uid = int(os.environ.get('SUDO_UID', '0'))
        if uid <= 0:
            raise Error('Authorization must identify the requesting user.')
        user = pwd.getpwuid(uid).pw_name
        action, path, finger, owner = sys.argv[1:]
        bus = connect('unix:path=/run/dbus/system_bus_socket')
        candidates = {d.path: d for d in devices(bus)}
        if path not in candidates or candidates[path].owner != owner:
            raise Error('Reader disconnected. Refresh the reader list.')
        result = candidates[path].run(action, finger, user, emit, sys.stdin.fileno())
        emit(result | {'done': True})
        return 0 if result['ok'] else 1
    except (Error, dbus.DBusException, ValueError, KeyError) as error:
        emit({'done': True, 'ok': False, 'message': str(error) if isinstance(error, Error) else error_text(error)})
        return 1
    except BrokenPipeError:
        return 1
