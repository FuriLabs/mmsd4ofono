PREFIX ?= /usr
LIBDIR ?= $(PREFIX)/lib
BINDIR ?= $(PREFIX)/bin
SBINDIR ?= $(PREFIX)/sbin
SYSTEMD_USER_DIR = /usr/lib/systemd/user
SYSTEMD_SYSTEM_DIR = /usr/lib/systemd/system

MAIN = main.py
MMSD_DIRS = mmsd mmsdecoder routectld mmscli
DBUS_XML = dbus/dbus.xml dbus/ofono.xml dbus/ofono_modem.xml dbus/ofono_operator.xml dbus/ofono_context.xml

.PHONY: all install uninstall

all:
	@echo "Run 'make install' to install the files."

install:
	install -d $(DESTDIR)$(LIBDIR)/mmsd/
	install -d $(DESTDIR)$(BINDIR)/
	install -d $(DESTDIR)$(SBINDIR)/
	install -d $(DESTDIR)$(SYSTEMD_USER_DIR)/
	install -d $(DESTDIR)$(SYSTEMD_SYSTEM_DIR)/

	install -m 755 $(MAIN) $(DESTDIR)$(LIBDIR)/mmsd/
	cp -r $(MMSD_DIRS) $(DESTDIR)$(LIBDIR)/mmsd/

	install -m 644 $(DBUS_XML) $(DESTDIR)$(LIBDIR)/mmsd/

	install -m 644 data/mmsd.service $(DESTDIR)$(SYSTEMD_USER_DIR)/
	install -m 644 data/routectld.service $(DESTDIR)$(SYSTEMD_SYSTEM_DIR)/

	ln -sf ../lib/mmsd/main.py $(DESTDIR)$(BINDIR)/mmsd
	ln -sf ../lib/mmsd/mmscli/mmscli $(DESTDIR)$(BINDIR)/mmscli
	ln -sf ../lib/mmsd/routectld/routectld.py $(DESTDIR)$(SBINDIR)/routectld

uninstall:
	rm -rf $(DESTDIR)$(LIBDIR)/mmsd/
	rm -f $(DESTDIR)$(BINDIR)/mmsd
	rm -f $(DESTDIR)$(BINDIR)/mmscli
	rm -f $(DESTDIR)$(SBINDIR)/routectld
	rm -f $(DESTDIR)$(SYSTEMD_USER_DIR)/mmsd.service
	rm -f $(DESTDIR)$(SYSTEMD_SYSTEM_DIR)/mmsd4ofono.routectld.service
