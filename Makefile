# integrations: the registry of outside services, and its scan.

# The suite runs against src/ and bin/, with nothing installed.
FUNCTIONAL_ENV = PYTHONPATH=$(CURDIR)/src INTEGRATIONS_BIN_DIR=$(CURDIR)/bin
EDITABLE =
include pubkit-python.mk

check: sync-check functional ## Everything CI runs
