# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
"""Freeze the reviewed app-only installer and firmware; no network or device I/O."""
import gzip
import hashlib
import io
import json
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
WORK = ROOT / 'work/meshticulum-abc-deployment-20261009'
PRIOR = ROOT / 'work/ab-serial-20260927/firmware-source/.pio/build/lantern_heltec_usb_r7/firmware.bin'
PRIOR_SHA = '69840340b52486ef9ab3f3747d90553b5d9dcc6b3f8d8b5fc0c49bdd92359a11'
UNITS = {'unit-a': 'F8:5B:1B:A5:F6:08', 'unit-b': '44:1B:F6:62:56:54'}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    evidence = json.loads((WORK / 'FIRMWARE_BUILD_EVIDENCE.json').read_text())
    build = next(v for v in evidence['results'] if v['target'] == 'mtbench_heltec_v4')
    assert build['returncode'] == 0
    artifact = build['artifacts']['firmware.bin']
    files = {'install_ab.py': (HERE / 'install_ab.py').read_bytes(),
             'ab_bootstrap.py': (HERE / 'ab_bootstrap.py').read_bytes(),
             'firmware.bin': Path(artifact['path']).read_bytes(), 'rollback-r7.bin': PRIOR.read_bytes()}
    assert sha(files['firmware.bin']) == artifact['sha256']
    assert sha(files['rollback-r7.bin']) == PRIOR_SHA
    for name in ('__init__.py', 'flash_app.py', 'linux_hat.py', 'serial_port.py', 'serial_protocol.py'):
        files['meshticulum_bench/' + name] = (ROOT / 'src/meshticulum/bench' / name).read_bytes()
    manifest = {'schema': 'meshticulum.ab-flash-bundle.v1', 'units': UNITS,
        'firmware_identity': build['firmware_identity'], 'board_id': 1, 'target': build['target'],
        'files': {name: sha(data) for name, data in sorted(files.items())},
        'source_manifest_sha256': evidence['source_manifest_sha256'],
        'required_python': '/root/esp-venv-r3/bin/python with esptool 4.x and pyserial',
        'scope': 'Application sectors only. No NVS/SPIFFS backup export or writes. No ARM/TX commands, service or access-policy changes.',
        'rollback': 'On-device double-read exact overwritten-sector backup plus pinned current r7 comparison; recover only on the same unit/USB/bundle.'}
    files['manifest.json'] = (json.dumps(manifest, sort_keys=True, indent=2) + '\n').encode()
    stream = io.BytesIO()
    with gzip.GzipFile(fileobj=stream, mode='wb', mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode='w', format=tarfile.USTAR_FORMAT) as archive:
            for name, data in sorted(files.items()):
                item = tarfile.TarInfo(name)
                item.size, item.mode, item.uid, item.gid, item.mtime = len(data), 0o600, 0, 0, 0
                archive.addfile(item, io.BytesIO(data))
    data = stream.getvalue()
    digest = sha(data)
    destination = WORK / ('ab-flash-' + digest[:16])
    destination.mkdir(mode=0o700, exist_ok=True)
    archive = destination / 'bundle.tar.gz'
    if archive.exists():
        assert archive.read_bytes() == data
    else:
        archive.write_bytes(data)
    (destination / 'ab_bootstrap.py').write_bytes(files['ab_bootstrap.py'])
    result = {'schema': 'meshticulum.ab-flash-bundle-receipt.v1', 'archive': str(archive),
        'sha256': digest, 'bytes': len(data), 'firmware_identity': build['firmware_identity'],
        'manifest_sha256': sha(files['manifest.json']), 'source_files': manifest['files'],
        'bootstrap_sha256': sha(files['ab_bootstrap.py']), 'staged': False, 'flashed': False}
    (destination / 'BUNDLE.json').write_text(json.dumps(result, indent=2) + '\n')
    (WORK / 'AB_BUNDLE.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
