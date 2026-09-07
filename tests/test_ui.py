import unittest
from unittest.mock import Mock, patch
from fingerprint_tui import ui


class FlowTests(unittest.TestCase):
    def run_flow(self, prints, command='setup', outcomes=(True,), choice=0):
        device = Mock(name='reader')
        device.name, device.capacity = 'Generic reader', None
        device.fingers.return_value = prints
        screen = Mock()
        screen.choose.return_value = choice
        screen.operate.side_effect = outcomes
        with patch.object(ui, 'Screen', return_value=screen), patch.object(ui, 'connect'), patch.object(ui, 'devices', return_value=[device]):
            result = ui.run(None, command, None)
        return result, device, screen

    def test_setup_enrolls_and_verifies_same_finger(self):
        result, device, screen = self.run_flow([], outcomes=(True, True))
        self.assertEqual(result, 0)
        self.assertEqual([c.args for c in screen.operate.call_args_list],
                         [(device, 'enroll', 'right-thumb'), (device, 'verify', 'right-thumb')])
        self.assertEqual(screen.authorize.call_count, 1)

    def test_existing_enrollment_is_not_replaced_by_setup(self):
        result, device, screen = self.run_flow(['left-thumb'])
        self.assertEqual(result, 0)
        screen.operate.assert_called_once_with(device, 'verify', 'left-thumb')

    def test_setup_failure_never_returns_success(self):
        for outcomes in ((False,), (True, False)):
            result, _, _ = self.run_flow([], outcomes=outcomes)
            self.assertEqual(result, 1)

    def test_delete_confirmation_defaults_to_keep(self):
        result, _, screen = self.run_flow(['right-thumb'], command='delete')
        self.assertEqual(result, 130)
        screen.operate.assert_not_called()
        self.assertIn('last fingerprint', screen.choose.call_args.args[2])

    def test_selection_cancel_never_starts_operation(self):
        result, _, screen = self.run_flow([], choice=None)
        self.assertEqual(result, 130)
        screen.operate.assert_not_called()

    def test_no_reader_explains_and_exits(self):
        screen = Mock()
        with patch.object(ui, 'Screen', return_value=screen), patch.object(ui, 'connect'), patch.object(ui, 'devices', return_value=[]):
            self.assertEqual(ui.run(None, 'manage', None), 1)
        screen.notice.assert_called_once()
        screen.authorize.assert_not_called()
