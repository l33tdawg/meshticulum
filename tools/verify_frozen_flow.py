"""Check accepted source pins without importing code or touching hardware."""
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PINS = {
    'flow.py': '9a8d4bb3bea2f69581d80ecd31fda7692c2435fe1363be53564d5281485cb857',
    'flow_port.py': '57596421d916f0c49d61599653b5ab75154d64e5bc806ba1444fa5905ad025ca',
}
for name, expected in PINS.items():
    actual = hashlib.sha256((ROOT / 'src/meshticulum/flow' / name).read_bytes()).hexdigest()
    if actual != expected:
        raise SystemExit('Accepted source changed: ' + name)
print('Accepted flow source pins verified.')
