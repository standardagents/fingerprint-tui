"""Synthetic fprintd service. Runs only on an explicitly supplied test bus."""
import os
import sys
import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

IFACE = 'net.reactivated.Fprint.Device'
CONTROL = 'org.example.FingerprintTest'
DBusGMainLoop(set_as_default=True)
bus = dbus.bus.BusConnection(sys.argv[1])
name = dbus.service.BusName('net.reactivated.Fprint', bus)


class Device(dbus.service.Object):
    def __init__(self, number, title, stages, scan):
        self.path = f'/net/reactivated/Fprint/Device/{number}'
        super().__init__(bus, self.path)
        self.title, self.stages, self.scan = title, stages, scan
        self.Reset('normal')

    @dbus.service.method(CONTROL, in_signature='s')
    def Reset(self, mode):
        self.mode = mode
        self.prints = ['right-index-finger']
        self.user = None
        self.action = None
        self.calls = []

    @dbus.service.method(CONTROL, out_signature='as')
    def Calls(self):
        return self.calls

    @dbus.service.method(CONTROL, in_signature='as')
    def SetFingers(self, fingers):
        self.prints = list(fingers)

    @dbus.service.method(dbus.PROPERTIES_IFACE, in_signature='s', out_signature='a{sv}')
    def GetAll(self, interface):
        return {'name': self.title, 'num-enroll-stages': dbus.Int32(self.stages), 'scan-type': self.scan}

    @dbus.service.method(IFACE, in_signature='s', out_signature='as')
    def ListEnrolledFingers(self, user):
        if self.mode == 'check-claimed' and self.user is None:
            raise dbus.DBusException('Check requires claim', name='net.reactivated.Fprint.Error.ClaimDevice')
        if not self.prints:
            raise dbus.DBusException('Empty', name='net.reactivated.Fprint.Error.NoEnrolledPrints')
        return self.prints

    @dbus.service.method(IFACE, in_signature='s')
    def Claim(self, user):
        if self.mode == 'busy':
            raise dbus.DBusException('Busy', name='net.reactivated.Fprint.Error.AlreadyInUse')
        if self.mode == 'denied':
            raise dbus.DBusException('Denied', name='net.reactivated.Fprint.Error.PermissionDenied')
        if self.mode == 'race':
            self.prints.append('right-middle-finger')
        self.calls.append('Claim:' + user)
        self.user = user

    @dbus.service.method(IFACE)
    def Release(self):
        self.calls.append('Release')
        self.user = None
        self.action = None

    @dbus.service.signal(IFACE, signature='sb')
    def EnrollStatus(self, status, done):
        pass

    @dbus.service.signal(IFACE, signature='sb')
    def VerifyStatus(self, status, done):
        pass

    @dbus.service.method(IFACE, in_signature='s')
    def EnrollStart(self, finger):
        self.calls.append('EnrollStart:' + finger)
        self.action = 'enroll'
        if self.mode == 'idle':
            return
        failures = {'full': 'enroll-data-full', 'duplicate': 'enroll-duplicate',
                    'disconnect': 'enroll-disconnected', 'fail': 'enroll-failed'}
        if self.mode in failures:
            GLib.timeout_add(50, lambda: self.EnrollStatus(failures[self.mode], True))
            return
        def complete():
            if self.action != 'enroll':
                return False
            if self.mode != 'unconfirmed':
                self.prints.append(finger)
            self.EnrollStatus('enroll-completed', True)
            return False
        delay = 800 if os.environ.get('FINGERPRINT_TEST_VISUAL') else 20
        for stage in range(self.stages):
            GLib.timeout_add(delay * (stage + 1), self.stage)
        GLib.timeout_add(delay * (self.stages + 1), complete)

    def stage(self):
        if self.action == 'enroll':
            self.EnrollStatus('enroll-stage-passed', False)
        return False

    @dbus.service.method(IFACE)
    def EnrollStop(self):
        self.calls.append('EnrollStop')
        self.action = None

    @dbus.service.method(IFACE, in_signature='s')
    def VerifyStart(self, finger):
        self.calls.append('VerifyStart:' + finger)
        self.action = 'verify'
        GLib.timeout_add(50, lambda: self.VerifyStatus('verify-no-match' if self.mode == 'nomatch' else 'verify-match', True))

    @dbus.service.method(IFACE)
    def VerifyStop(self):
        self.calls.append('VerifyStop')
        self.action = None

    @dbus.service.method(IFACE, in_signature='s')
    def DeleteEnrolledFinger(self, finger):
        self.calls.append('Delete:' + finger)
        if self.mode == 'unsupported-delete':
            raise dbus.DBusException('Unsupported', name='org.freedesktop.DBus.Error.UnknownMethod')
        if self.mode != 'unconfirmed':
            self.prints.remove(finger)


readers = [Device(1, 'Apple Touch ID', 6, 'press'), Device(2, 'Generic swipe reader', 8, 'swipe')]


class Manager(dbus.service.Object):
    @dbus.service.method('net.reactivated.Fprint.Manager', out_signature='ao')
    def GetDevices(self):
        return [d.path for d in readers]


manager = Manager(bus, '/net/reactivated/Fprint/Manager')
print('READY', flush=True)
GLib.MainLoop().run()
