import os
from pathlib import Path
import subprocess
import sys
import unittest

import dbus
from fingerprint_tui.backend import connect, devices, Error
from fingerprint_tui.ui import message


class BackendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.daemon = subprocess.Popen(['dbus-daemon', '--session', '--nofork', '--print-address=1'], stdout=subprocess.PIPE, text=True)
        address = cls.daemon.stdout.readline().strip()
        cls.mock = subprocess.Popen([sys.executable, str(Path(__file__).with_name('mock_service.py')), address], stdout=subprocess.PIPE, text=True)
        assert cls.mock.stdout.readline().strip() == 'READY'
        cls.bus = connect(address)

    @classmethod
    def tearDownClass(cls):
        cls.bus.close()
        for process in (cls.mock, cls.daemon):
            process.terminate()
            process.wait(timeout=5)
            process.stdout.close()

    def setUp(self):
        self.readers = devices(self.bus)
        self.controls = [dbus.Interface(d.object, 'org.example.FingerprintTest') for d in self.readers]
        for control in self.controls:
            control.Reset('normal')

    def operation(self, action='enroll', finger='right-middle-finger', index=0, **kwargs):
        self.events = []
        return self.readers[index].run(action, finger, 'test-user', self.events.append, **kwargs)

    def test_touch_id_limit_and_generic_capacity(self):
        self.assertEqual(self.readers[0].capacity, 3)
        self.assertIsNone(self.readers[1].capacity)
        fingers = ['right-index-finger', 'right-ring-finger', 'left-thumb']
        for c in self.controls:
            c.SetFingers(fingers)
        with self.assertRaises(Error):
            self.operation()
        self.assertEqual(list(self.controls[0].Calls()), ['Claim:test-user', 'Release'])
        self.assertTrue(self.operation(index=1)['ok'])

    def test_success_and_device_isolation(self):
        self.assertTrue(self.operation()['ok'])
        self.assertEqual(self.readers[1].fingers(), ['right-index-finger'])
        progress = [e['progress'] for e in self.events if e['status'] == 'enroll-stage-passed']
        self.assertEqual(progress, [16, 33, 50, 66, 83, 99])
        self.assertEqual(list(self.controls[0].Calls())[-2:], ['EnrollStop', 'Release'])

    def test_generic_swipe_stages(self):
        self.assertEqual(self.readers[1].scan, 'swipe')
        self.assertTrue(self.operation(index=1)['ok'])
        self.assertEqual(self.events[0]['progress'], 12)

    def test_reenrollment_never_replaces_print(self):
        with self.assertRaises(Error):
            self.operation(finger='right-index-finger')
        self.assertEqual(list(self.controls[0].Calls()), ['Claim:test-user', 'Release'])

    def test_checks_remain_inside_exclusive_claim(self):
        self.controls[0].Reset('check-claimed')
        self.assertTrue(self.operation()['ok'])

    def test_concurrent_enrollment_is_not_replaced(self):
        self.controls[0].Reset('race')
        with self.assertRaisesRegex(Error, 'already enrolled'):
            self.operation()
        self.assertEqual(list(self.controls[0].Calls()), ['Claim:test-user', 'Release'])
        self.assertEqual(self.readers[0].fingers(), ['right-index-finger', 'right-middle-finger'])

    def test_terminal_failures_release(self):
        for mode in ('full', 'duplicate', 'disconnect', 'fail'):
            with self.subTest(mode=mode):
                self.controls[0].Reset(mode)
                self.assertFalse(self.operation()['ok'])
                self.assertEqual(self.readers[0].fingers(), ['right-index-finger'])
                self.assertEqual(list(self.controls[0].Calls())[-2:], ['EnrollStop', 'Release'])

    def test_denied_or_busy_never_starts(self):
        for mode in ('denied', 'busy'):
            self.controls[0].Reset(mode)
            with self.assertRaises(dbus.DBusException):
                self.operation()
            self.assertEqual(list(self.controls[0].Calls()), [])

    def test_completion_requires_persisted_label(self):
        self.controls[0].Reset('unconfirmed')
        with self.assertRaisesRegex(Error, 'confirm'):
            self.operation()
        self.assertEqual(message({'status': 'enroll-completed'}), 'Finishing enrollment…')

    def test_exact_delete_keeps_other_fingers_and_reader(self):
        self.controls[0].SetFingers(['right-index-finger', 'left-thumb'])
        self.assertTrue(self.operation('delete', 'left-thumb')['ok'])
        self.assertEqual(self.readers[0].fingers(), ['right-index-finger'])
        self.assertEqual(self.readers[1].fingers(), ['right-index-finger'])
        self.assertEqual(list(self.controls[0].Calls()), ['Claim:test-user', 'Delete:left-thumb', 'Release'])

    def test_unsupported_delete_has_no_delete_all_fallback(self):
        self.controls[0].Reset('unsupported-delete')
        with self.assertRaises(dbus.DBusException):
            self.operation('delete', 'right-index-finger')
        self.assertEqual(self.readers[0].fingers(), ['right-index-finger'])
        self.assertEqual(list(self.controls[0].Calls())[-1], 'Release')

    def test_unconfirmed_deletion_fails(self):
        self.controls[0].Reset('unconfirmed')
        with self.assertRaisesRegex(Error, 'confirm'):
            self.operation('delete', 'right-index-finger')

    def test_verify_match_and_no_match(self):
        self.assertTrue(self.operation('verify', 'right-index-finger')['ok'])
        self.controls[0].Reset('nomatch')
        self.assertFalse(self.operation('verify', 'right-index-finger')['ok'])
        self.assertEqual(list(self.controls[0].Calls())[-2:], ['VerifyStop', 'Release'])

    def test_timeout_releases_reader(self):
        self.controls[0].Reset('idle')
        self.assertEqual(self.operation(timeout=1)['status'], 'timeout')
        self.assertEqual(list(self.controls[0].Calls())[-2:], ['EnrollStop', 'Release'])

    def test_ui_exit_cancels_and_releases_reader(self):
        self.controls[0].Reset('idle')
        read, write = os.pipe()
        os.close(write)
        try:
            self.assertEqual(self.operation(cancel_fd=read)['status'], 'cancelled')
        finally:
            os.close(read)
        self.assertEqual(list(self.controls[0].Calls())[-2:], ['EnrollStop', 'Release'])


if __name__ == '__main__':
    unittest.main()
