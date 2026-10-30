"""Check accepted source pins without importing code or touching hardware."""
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PINS = {
    'flow.py': '12dc626a9e1e5cc2120d2e59f1b857164cd74c4f59c89dec64c1637fe22b1e65',
    'flow_port.py': 'ea29289b960ca895f54ccb46669b476a50bae79ab9a6b4060342da8c5cfacf07',
}
for name, expected in PINS.items():
    actual = hashlib.sha256((ROOT / 'src/meshticulum/flow' / name).read_bytes()).hexdigest()
    if actual != expected:
        raise SystemExit('Accepted source changed: ' + name)
print('Accepted flow source pins verified.')
