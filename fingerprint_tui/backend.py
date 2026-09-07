"""Standard fprintd API. No device-specific services, limits, or state files."""
import dbus
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

SERVICE = 'net.reactivated.Fprint'
INTERFACE = SERVICE + '.Device'
FINGERS = tuple(f'{hand}-{finger}' for hand in ('right', 'left') for finger in
                ('thumb', 'index-finger', 'middle-finger', 'ring-finger', 'little-finger'))


class Error(Exception):
    pass


def error_text(error):
    name = error.get_dbus_name().rsplit('.', 1)[-1] if isinstance(error, dbus.DBusException) else ''
    return {
        'NoSuchDevice': 'Reader disconnected. Refresh the reader list.',
        'AlreadyInUse': 'Reader is busy. Close other fingerprint prompts and retry.',
        'PermissionDenied': 'Authorization was denied. No change was confirmed.',
        'ServiceUnknown': 'Fingerprint service is unavailable. Run fingerprint setup first.',
        'Spawn.ChildExited': 'Fingerprint service could not start.',
        'UnknownMethod': 'This fprintd version does not support the requested operation.',
        'NoReply': 'Fingerprint service did not respond.',
        'NoEnrolledPrints': 'No fingerprints are enrolled on this reader.',
    }.get(name, 'Fingerprint operation failed. No change was confirmed.')


def connect(address=None):
    DBusGMainLoop(set_as_default=True)
    # Privileged callers supply the fixed system socket, never an environment address.
    return dbus.bus.BusConnection(address) if address else dbus.SystemBus()


def devices(bus):
    manager = dbus.Interface(bus.get_object(SERVICE, '/net/reactivated/Fprint/Manager'),
                             SERVICE + '.Manager')
    return [Device(bus, str(path)) for path in manager.GetDevices(timeout=10)]


class Device:
    def __init__(self, bus, path):
        self.path = path
        self.owner = str(bus.get_name_owner(SERVICE))
        self.object = bus.get_object(SERVICE, path)
        self.api = dbus.Interface(self.object, INTERFACE)
        properties = dbus.Interface(self.object, dbus.PROPERTIES_IFACE).GetAll(INTERFACE, timeout=10)
        self.name = str(properties['name'])
        self.stages = max(0, int(properties['num-enroll-stages']))
        self.scan = str(properties['scan-type'])
        # The T1Bridge libfprint driver publishes this exact device name.
        self.capacity = 3 if self.name == 'Apple Touch ID' else None

    def fingers(self, user=''):
        try:
            return [str(f) for f in self.api.ListEnrolledFingers(user, timeout=10)]
        except dbus.DBusException as error:
            if error.get_dbus_name().endswith('.NoEnrolledPrints'):
                return []
            raise

    def run(self, action, finger, user, emit, cancel_fd=None, timeout=120):
        if action not in ('enroll', 'verify', 'delete') or finger not in FINGERS:
            raise Error('Choose a valid operation and finger.')
        before = self.fingers(user)
        if action == 'enroll' and self.capacity is not None and len(before) >= self.capacity:
            raise Error('Touch ID: 3 of 3 fingerprints enrolled. Remove one before adding another.')
        if action == 'enroll' and finger in before:
            raise Error('That finger is already enrolled. Remove it explicitly before replacing it.')
        if action != 'enroll' and finger not in before:
            raise Error('That finger is no longer enrolled. Refresh the list.')
        loop = GLib.MainLoop()
        result = None
        passed = 0
        started = False
        claimed = False
        signal = None
        sources = []

        def finish(status):
            nonlocal result
            if result is None:
                result = status
                loop.quit()
            return False

        def status(code, done):
            nonlocal passed
            code = str(code)
            if result is not None:
                return
            if code == 'enroll-stage-passed':
                passed += 1
            progress = min(99, passed * 100 // self.stages) if self.stages else None
            emit({'status': code, 'progress': progress})
            if done:
                finish(code)

        def cancel(_fd, _condition):
            finish('cancelled')
            return True  # removed in finally, including EOF after a killed UI

        try:
            self.api.Claim(user, timeout=10)
            claimed = True
            if action == 'delete':
                # Never substitute DeleteEnrolledFingers: it erases every print.
                self.api.DeleteEnrolledFinger(finger, timeout=10)
                result = 'deleted'
            else:
                signal = self.object.connect_to_signal(
                    'EnrollStatus' if action == 'enroll' else 'VerifyStatus',
                    status, dbus_interface=INTERFACE)
                sources.append(GLib.timeout_add_seconds(timeout, lambda: (finish('timeout'), True)[1]))
                if cancel_fd is not None:
                    sources.append(GLib.io_add_watch(cancel_fd, GLib.PRIORITY_DEFAULT, GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR, cancel))
                method = self.api.EnrollStart if action == 'enroll' else self.api.VerifyStart
                method(finger, timeout=10)
                started = True
                if result is None:
                    loop.run()
        finally:
            for source in sources:
                GLib.source_remove(source)
            if signal:
                signal.remove()
            if claimed:
                try:
                    if started:
                        stop = self.api.EnrollStop if action == 'enroll' else self.api.VerifyStop
                        stop(timeout=10)
                finally:
                    self.api.Release(timeout=10)
        expected = {'enroll': 'enroll-completed', 'verify': 'verify-match', 'delete': 'deleted'}[action]
        if result != expected:
            return {'status': result or 'failed', 'ok': False}
        after = self.fingers(user)
        if action == 'enroll' and (finger not in after or not set(before) <= set(after)):
            raise Error('Could not confirm the new fingerprint. Refresh the list before retrying.')
        if action == 'delete' and set(after) != set(before) - {finger}:
            raise Error('Could not confirm exact removal. Refresh the list before retrying.')
        return {'status': expected, 'ok': True, 'progress': 100}
