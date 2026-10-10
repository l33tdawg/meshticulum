# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
"""Freeze the reviewed flow plus bench adapters; never grants RF authority."""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import tarfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / 'build/runtime'
FLOW_PINS = {'flow.py': '9a8d4bb3bea2f69581d80ecd31fda7692c2435fe1363be53564d5281485cb857',
             'flow_port.py': '57596421d916f0c49d61599653b5ab75154d64e5bc806ba1444fa5905ad025ca'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-test-report', type=Path)
    parser.add_argument('--output-dir', type=Path, default=WORK)
    args = parser.parse_args()
    work = args.output_dir.resolve()
    work.mkdir(parents=True, exist_ok=True)
    test_report = test_hash = None
    if args.source_test_report:
        report = args.source_test_report.resolve()
        suites = list(ET.fromstring(report.read_bytes()).iter('testsuite'))
        if not suites or not sum(int(s.get('tests', 0)) for s in suites) or any(
                int(s.get('failures', 0)) or int(s.get('errors', 0)) for s in suites):
            raise SystemExit('Source test report is empty or failing.')
        test_report = str(report.relative_to(work))
        test_hash = hashlib.sha256(report.read_bytes()).hexdigest()
    bootstrap = (ROOT / 'firmware/meshticulum-bench/runtime_bootstrap.py').read_bytes()
    files = {'bench_node.py': (ROOT / 'firmware/meshticulum-bench/bench_node.py').read_bytes(),
             'meshticulum/__init__.py': (ROOT / 'src/meshticulum/__init__.py').read_bytes(),
             'meshticulum/resources/__init__.py': (ROOT / 'src/meshticulum/resources/__init__.py').read_bytes(),
             'meshticulum/resources/resource.py': (ROOT / 'src/meshticulum/resources/resource.py').read_bytes()}
    for package in ('bench', 'contracts', 'scheduler', 'flow',
                    'meshticulum_contracts', 'meshticulum_scheduler'):
        for path in sorted((ROOT / 'src/meshticulum' / package).glob('*.py')):
            if path.name == 'flash_app.py': continue
            data = path.read_bytes()
            if package == 'flow' and path.name in FLOW_PINS and hashlib.sha256(data).hexdigest() != FLOW_PINS[path.name]:
                raise SystemExit('Accepted flow source changed; review required before deployment.')
            files[str(path.relative_to(ROOT / 'src'))] = data
    for name, data in files.items(): compile(data, name, 'exec')
    manifest = {'schema': 'meshticulum.bench-runtime-bundle.v1', 'firmware': 'mtb1-9816f92488',
        'bootstrap_sha256': hashlib.sha256(bootstrap).hexdigest(),
        'files': {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())},
        'activation': 'Installation only inspects; a separate root-provisioned finite grant is required to arm.',
        'private_sage_imports': False, 'private_services_enabled': False,
        'public_service_formats': ['MR1.request', 'MR1.response'],
        'radio_mtu_bytes': 255, 'flow_payload_bytes': 193}
    files['manifest.json'] = json.dumps(manifest, sort_keys=True, indent=2).encode() + b'\n'
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode='w', format=tarfile.USTAR_FORMAT) as archive:
        for name, data in sorted(files.items()):
            entry = tarfile.TarInfo(name); entry.size = len(data); entry.mode = 0o600
            archive.addfile(entry, io.BytesIO(data))
    data = gzip.compress(raw.getvalue(), mtime=0)
    digest = hashlib.sha256(data).hexdigest()
    directory = work / ('runtime-' + digest[:16]); directory.mkdir(exist_ok=True)
    archive = directory / 'runtime.tar.gz'
    if archive.exists(): assert archive.read_bytes() == data
    else: archive.write_bytes(data)
    target = directory / 'runtime_bootstrap.py'
    if target.exists(): assert target.read_bytes() == bootstrap
    else: target.write_bytes(bootstrap)
    record = {'schema': 'meshticulum.runtime-build.v1', 'archive': str(archive), 'sha256': digest,
        'bytes': len(data), 'bootstrap_sha256': hashlib.sha256(bootstrap).hexdigest(),
        'manifest': manifest, 'source_tests': test_report, 'source_tests_sha256': test_hash, 'rf_tests': None,
        'installed': False}
    latest = work / 'RUNTIME_BUNDLE.json'
    if latest.exists():
        prior = json.loads(latest.read_text())
        (work / ('RUNTIME_BUNDLE-' + prior['sha256'][:16] + '.json')).write_bytes(latest.read_bytes())
    for output in (directory / 'BUNDLE.json', latest):
        output.write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps({k: v for k, v in record.items() if k != 'manifest'}))


if __name__ == '__main__': main()
