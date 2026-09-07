# Fingerprint TUI

Add, verify, and remove fingerprints through fprintd. Supports Touch ID through T1Bridge and other fprintd-supported readers, including swipe sensors and multiple readers. Touch ID shows its three-fingerprint per-user limit; other readers are not given that limit.

Run `fingerprint-tui`. Use arrow keys and Enter to choose a reader, hand, and finger; Esc goes back or cancels. Enrollment fills the fingerprint illustration using the reader's reported stages. Success requires a confirmed result. Removing the last fingerprint requires confirmation; keep password access.

Requires Python, dbus-python, PyGObject, sudo, and a working fprintd driver. The interface runs as your normal user. A short-lived sudo helper authorizes before claiming the reader, avoiding competing authentication prompts. It changes only that user's fingerprints on the selected reader. It never edits PAM or installs drivers.

Omarchy handles driver installation and authentication setup. The TUI package can be installed without a reader or fprintd; opening it explains when setup is missing.

```
fingerprint-tui setup   # enroll if needed, then verify; does not change PAM
fingerprint-tui enroll
fingerprint-tui verify right-index-finger
fingerprint-tui delete
```

`make check` uses a private D-Bus mock; it never accesses your readers. `make install DESTDIR=…` stages the package. Launchers use `/usr/lib/fingerprint-tui`; install under `/usr`.

The hand/finger flow and fingerprint artwork originate in Omarchy. See LICENSE.
