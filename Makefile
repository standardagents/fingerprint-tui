PREFIX ?= /usr
LIBDIR = $(PREFIX)/lib/fingerprint-tui

.PHONY: check install
check:
	python3 -m unittest discover -s tests -v

install:
	install -Dm755 bin/fingerprint-tui $(DESTDIR)$(PREFIX)/bin/fingerprint-tui
	install -Dm755 bin/worker $(DESTDIR)$(LIBDIR)/worker
	install -d $(DESTDIR)$(LIBDIR)/fingerprint_tui
	install -m644 fingerprint_tui/*.py $(DESTDIR)$(LIBDIR)/fingerprint_tui/
	install -Dm644 LICENSE $(DESTDIR)$(PREFIX)/share/licenses/fingerprint-tui/LICENSE
