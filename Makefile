PREFIX ?= /usr
LIBDIR ?= $(PREFIX)/lib
BINDIR ?= $(PREFIX)/bin
SBINDIR ?= $(PREFIX)/sbin
SHAREDIR ?= $(PREFIX)/share
SYSTEMD_USER_DIR = /usr/lib/systemd/user
SYSTEMD_SYSTEM_DIR = /usr/lib/systemd/system

MAIN = main.py
MMSD_DIRS = mmsd mmsdecoder routectld mmscli
DBUS_XML = dbus/dbus.xml
OFONO_XML_FILES = dbus/ofono.xml dbus/ofono_modem.xml dbus/ofono_operator.xml dbus/ofono_context.xml
SYSTEMD_FILES = debian/mmsd.service debian/mmsd4ofono.routectld.service

.PHONY: all install uninstall

all:
	@echo "Run 'make install' to install the files."

install:
	install -d $(LIBDIR)/mmsd/
	install -d $(SYSTEMD_USER_DIR)/
	install -d $(SYSTEMD_SYSTEM_DIR)/
	install -d $(SHAREDIR)/mmsd/scripts/

	install -m 755 $(MAIN) $(LIBDIR)/mmsd/
	cp -r $(MMSD_DIRS) $(LIBDIR)/mmsd/

	install -m 644 $(DBUS_XML) $(LIBDIR)/mmsd/
	install -m 644 $(OFONO_XML_FILES) $(LIBDIR)/mmsd/

	install -m 644 debian/mmsd.service $(SYSTEMD_USER_DIR)/
	install -m 644 debian/mmsd4ofono.routectld.service $(SYSTEMD_SYSTEM_DIR)/

	cp -r tests/* $(SHAREDIR)/mmsd/scripts/

	ln -sf $(LIBDIR)/mmsd/main.py $(BINDIR)/mmsd
	ln -sf $(LIBDIR)/mmsd/mmscli/mmscli $(BINDIR)/mmscli
	ln -sf $(LIBDIR)/mmsd/routectld/routectld.py $(SBINDIR)/routectld

uninstall:
	rm -rf $(LIBDIR)/mmsd/
	rm -f $(BINDIR)/mmsd
	rm -f $(BINDIR)/mmscli
	rm -f $(SBINDIR)/routectld
	rm -f $(SYSTEMD_USER_DIR)/mmsd.service
	rm -f $(SYSTEMD_SYSTEM_DIR)/mmsd4ofono.routectld.service
	rm -rf $(SHAREDIR)/mmsd
