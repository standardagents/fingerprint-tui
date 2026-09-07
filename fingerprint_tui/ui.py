"""Keyboard-driven fingerprint management with standard fprintd progress."""
import argparse
import curses
import json
import os
import re
import selectors
import subprocess
import sys
import time
import textwrap

import dbus
from .art import braille
from .backend import Error, FINGERS, connect, devices, error_text

MESSAGES = {
    'enroll-stage-passed': 'Good scan. Lift and touch again.',
    'enroll-completed': 'Fingerprint enrolled.',
    'enroll-data-full': 'Reader storage is full. Remove a fingerprint before adding another.',
    'enroll-duplicate': 'That finger is already enrolled.',
    'verify-match': 'Fingerprint verified.',
    'verify-no-match': 'Fingerprint did not match. Try again.',
    'deleted': 'Fingerprint removed.',
    'cancelled': 'Cancelled. Refresh the list before retrying.',
    'timeout': 'Timed out. Refresh the list before retrying.',
}


def label(finger):
    return finger.replace('-', ' ').capitalize()


def clean(value):
    return ''.join(c for c in str(value) if c.isprintable())[:180]


def message(event):
    if event.get('status') == 'enroll-completed' and not event.get('done'):
        return 'Finishing enrollment…'
    if 'message' in event:
        return clean(event['message'])
    status = event.get('status', '')
    if status.endswith('disconnected'):
        return 'Reader disconnected. Refresh the list.'
    if status.endswith(('retry-scan', 'too-fast', 'swipe-too-short', 'finger-not-centered', 'remove-and-retry')):
        return 'Adjust your finger and scan again.'
    return MESSAGES.get(status, 'Fingerprint operation failed. Refresh the list before retrying.')


class Screen:
    def __init__(self, window):
        self.window = window
        curses.curs_set(0)
        curses.use_default_colors()
        self.window.timeout(100)
        for index, env, fallback in ((1, 'BORDER_FOREGROUND', 6), (2, 'FOREGROUND', 7),
                                     (3, 'GUM_FILTER_UNSELECTED_PREFIX_FOREGROUND', 8)):
            color = os.environ.get(env, '')
            if curses.COLORS >= 256 and re.fullmatch(r'#[0-9a-fA-F]{6}', color):
                rgb = [round(int(color[n:n+2], 16) / 255 * 5) for n in (1, 3, 5)]
                fallback = 16 + 36 * rgb[0] + 6 * rgb[1] + rgb[2]
            curses.init_pair(index, min(fallback, curses.COLORS - 1), -1)

    def line(self, row, text, color=2, center=True):
        rows, cols = self.window.getmaxyx()
        if not 0 <= row < rows - 1 or cols < 2:
            return
        text = clean(text)[:cols - 2]
        left = max(1, (cols - len(text)) // 2) if center else 2
        try:
            self.window.addstr(row, left, text, curses.color_pair(color))
        except curses.error:
            pass

    def frame(self, title, detail='', footer='↑/↓ move  ·  Enter select  ·  Esc back'):
        self.window.erase()
        rows, cols = self.window.getmaxyx()
        if rows < 16 or cols < 40:
            self.line(0, 'Resize terminal to at least 40 × 16', 1)
            self.window.refresh()
            return False
        self.line(1, title.upper(), 1)
        for index, line in enumerate(textwrap.wrap(clean(detail), cols - 4)[:3], 3):
            self.line(index, line, 3)
        self.line(rows - 2, footer, 3)
        return True

    def choose(self, title, options, detail=''):
        selected = 0
        while True:
            if self.frame(title, detail):
                rows, _ = self.window.getmaxyx()
                capacity = max(1, rows - 10)
                start = max(0, selected - capacity + 1)
                for row, text in enumerate(options[start:start + capacity], 7):
                    active = row - 7 + start == selected
                    self.line(row, ('● ' if active else '  ') + text, 1 if active else 2)
            self.window.refresh()
            key = self.window.getch()
            if key in (27, ord('q')):
                return None
            if key in (curses.KEY_UP, ord('k')):
                selected = (selected - 1) % len(options)
            if key in (curses.KEY_DOWN, ord('j')):
                selected = (selected + 1) % len(options)
            if key in (10, 13, curses.KEY_ENTER):
                return selected

    def notice(self, title, text):
        self.choose(title, ['Continue'], text)

    def authorize(self):
        curses.def_prog_mode()
        curses.endwin()
        try:
            print('Authorize this change with your fingerprint or password.', flush=True)
            subprocess.run(['/usr/bin/sudo', '-k'], check=True)
            status = subprocess.run(['/usr/bin/sudo', '-v'], check=False).returncode
        finally:
            curses.reset_prog_mode()
            curses.curs_set(0)
            self.window.clear()
        if status:
            raise Error('Authorization cancelled or denied.')

    def progress(self, device, finger, event, footer='Esc cancel'):
        rows, _ = self.window.getmaxyx()
        if self.frame(label(finger), getattr(device, 'summary', device.name), footer):
            art = braille()
            progress = event.get('progress')
            if rows >= 24:
                for i, row in enumerate(art):
                    filled = progress is not None and i < len(art) * progress // 100
                    self.line(5 + i, row, 1 if filled else 3)
            if progress is not None:
                width = 24
                filled = width * max(0, min(100, progress)) // 100
                self.line(rows - 6, '━' * filled + '─' * (width - filled) + f'  {progress}% ', 1)
            self.line(rows - 4, message(event))
        self.window.refresh()

    def operate(self, device, action, finger):
        instruction = ('Swipe the selected finger across the reader.' if device.scan == 'swipe'
                       else 'Lift and touch the selected finger repeatedly.')
        if action == 'delete':
            instruction = 'Removing the selected fingerprint…'
        event = {'message': instruction, 'progress': 0 if device.stages else None}
        result = None
        with subprocess.Popen(['/usr/bin/sudo', '-n', '/usr/lib/fingerprint-tui/worker',
                               action, device.path, finger, device.owner], stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
            selector = selectors.DefaultSelector()
            selector.register(process.stdout, selectors.EVENT_READ)
            buffer = b''
            deadline = time.monotonic() + 155
            cancelled = False
            try:
                while process.poll() is None or selector.get_map():
                    self.progress(device, finger, event)
                    if self.window.getch() in (27, ord('q')) and not cancelled:
                        process.stdin.close()  # EOF tells the helper to stop and release
                        cancelled = True
                        event = {'message': 'Cancelling and releasing the reader…'}
                    if time.monotonic() > deadline:
                        if not process.stdin.closed:
                            process.stdin.close()
                        raise Error('Fingerprint service did not finish. Refresh before retrying.')
                    for key, _ in selector.select(0.05):
                        chunk = os.read(key.fd, 4096)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        buffer += chunk
                        if len(buffer) > 16384:
                            raise Error('Invalid response from fingerprint helper.')
                        while b'\n' in buffer:
                            line, buffer = buffer.split(b'\n', 1)
                            event = json.loads(line)
                            if event.get('done'):
                                result = event
                success = process.wait() == 0 and result is not None and result.get('ok', False)
            finally:
                selector.close()
                if not process.stdin.closed:
                    process.stdin.close()
                # The helper bounds its D-Bus calls and active operation; EOF cancels it.
        if success:
            device.summary = device.name
            while True:
                self.progress(device, finger, result, 'Enter continue')
                if self.window.getch() in (10, 13, 27, curses.KEY_ENTER):
                    break
        else:
            self.notice('Not completed', message(result) if result else
                        'Authorization expired or the helper stopped. Refresh before retrying.')
        return success


def run(window, command, requested):
    screen = Screen(window)
    bus = connect()
    while True:
        try:
            available = devices(bus)
            if not available:
                screen.notice('No fingerprint reader', 'Connect a supported reader, then reopen Fingerprints.')
                return 1
            choice = 0 if len(available) == 1 else screen.choose('Choose a reader', [d.name for d in available])
            if choice is None:
                return 130
            device = available[choice]
            enrolled = device.fingers()
            detail = device.name
            if device.capacity is not None:
                detail += f' · {len(enrolled)}/{device.capacity} fingerprints · 3 per user'
            device.summary = detail
            action = command
            if command == 'setup':
                action = 'verify' if enrolled else 'enroll'
            if command == 'manage':
                choice = screen.choose('Fingerprints', ['Add fingerprint', 'Verify fingerprint',
                                       'Remove fingerprint', 'Refresh readers', 'Close'], detail)
                if choice is None or choice == 4:
                    return 0
                if choice == 3:
                    continue
                action = ('enroll', 'verify', 'delete')[choice]
            if action == 'enroll' and device.capacity is not None and len(enrolled) >= device.capacity:
                raise Error('Touch ID: 3 of 3 fingerprints enrolled. Remove one before adding another.')
            candidates = [f for f in FINGERS if (f not in enrolled if action == 'enroll' else f in enrolled)]
            if not candidates:
                raise Error('All finger labels are enrolled.' if action == 'enroll' else 'No fingerprints enrolled on this reader.')
            if requested and requested not in candidates:
                raise Error('That finger is unavailable for this operation.')
            screen.authorize()
            finger = requested
            if not finger:
                hands = [h for h in ('right', 'left') if any(f.startswith(h) for f in candidates)]
                hand = screen.choose('Choose a hand', [h.capitalize() + ' hand' for h in hands], detail)
                if hand is None:
                    if command != 'manage':
                        return 130
                    continue
                fingers = [f for f in candidates if f.startswith(hands[hand])]
                selection = screen.choose('Choose a finger', [label(f) for f in fingers],
                                          'Enrolled: ' + ', '.join(label(f) for f in enrolled) if enrolled else 'No fingerprints enrolled')
                if selection is None:
                    if command != 'manage':
                        return 130
                    continue
                finger = fingers[selection]
            if action == 'delete':
                detail = ('This is the last fingerprint on this reader. Keep password access.'
                          if len(enrolled) == 1 else 'Other fingerprints will be kept.')
                if screen.choose('Remove ' + label(finger) + '?', ['Keep fingerprint', 'Remove fingerprint'], detail) != 1:
                    if command != 'manage':
                        return 130
                    continue
            success = screen.operate(device, action, finger)
            if success and command == 'setup' and action == 'enroll':
                success = screen.operate(device, 'verify', finger)
            if command != 'manage':
                return 0 if success else 1
        except (dbus.DBusException, Error) as error:
            screen.notice('Fingerprints', str(error) if isinstance(error, Error) else error_text(error))
            if command != 'manage':
                return 1
            if screen.choose('Fingerprints', ['Retry', 'Close']) != 0:
                return 1


def main():
    parser = argparse.ArgumentParser(description='Manage fingerprints through fprintd.')
    parser.add_argument('command', nargs='?', default='manage', choices=('manage', 'setup', 'enroll', 'verify', 'delete'))
    parser.add_argument('finger', nargs='?', choices=FINGERS)
    args = parser.parse_args()
    if os.geteuid() == 0:
        parser.error('Run as your normal user; authorization is requested before each operation.')
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        parser.error('An interactive terminal is required.')
    try:
        return curses.wrapper(run, args.command, args.finger)
    except KeyboardInterrupt:
        return 130
    except (OSError, curses.error) as error:
        print('Fingerprint TUI: ' + clean(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
