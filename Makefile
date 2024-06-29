PREFIX ?= /usr
LIBDIR ?= $(PREFIX)/lib
BINDIR ?= $(PREFIX)/bin
SYSTEMD_DIR = /usr/lib/systemd/user

MAIN = main.py
MMSD_DIR = mmsd mmsdecoder
DBUS_XML = dbus/dbus.xml
OFONO_XML_FILES = dbus/ofono.xml dbus/ofono_modem.xml dbus/ofono_operator.xml dbus/ofono_context.xml

.PHONY: all install uninstall

all:
	@echo "Run 'make install' to install the files."

install:
	install -d $(LIBDIR)/mmsd/

	install -m 755 $(MAIN) $(LIBDIR)/mmsd/
	ln -sf $(LIBDIR)/mmsd/$(MAIN) $(BINDIR)/mmsd

	cp -r $(MMSD_DIR) $(LIBDIR)/mmsd/

	install -m 644 $(DBUS_XML) $(LIBDIR)/mmsd/
	install -m 644 $(OFONO_XML_FILES) $(LIBDIR)/mmsd/

uninstall:
	rm -rf $(LIBDIR)/mmsd/
	rm -f $(BINDIR)/mmsd
