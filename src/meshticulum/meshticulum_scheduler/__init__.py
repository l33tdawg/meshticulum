"""Import bridge for the unchanged accepted flow; do not copy class objects."""
import importlib
import sys

for _name in ('budget', 'scheduler', 'flow_attempt'):
    _module = importlib.import_module("meshticulum.scheduler." + _name)
    sys.modules[__name__ + "." + _name] = _module
    globals()[_name] = _module
