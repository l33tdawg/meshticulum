"""Import bridge for the unchanged accepted flow; do not copy class objects."""
import importlib
import sys

for _name in ('errors', 'identity', 'carrier', 'negotiation'):
    _module = importlib.import_module("meshticulum.contracts." + _name)
    sys.modules[__name__ + "." + _name] = _module
    globals()[_name] = _module
