"""Install a signed public pilot source at B or manifest-only recipient at C.

No transport, production identity or private store is opened. Recipient chunks
must arrive through the separately admitted service path.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import tempfile

from ..ip.journal import private
from ..ip.node import _read, _write
from ..resources import lamplighter as ll
from .pilot_provision import _directory
from .wire import require


def install(package, *, state, facts):
    fields={'schema','unit','host_boot_id','role','manifest_sha256','trust','files'}
    require(type(package) is dict and set(package)==fields
            and package['schema']=='meshticulum.pilot-public-seed.v1'
            and package['unit']==facts['unit'] and package['host_boot_id']==facts['host_boot_id'],
            'invalid_native_public_seed')
    role=package['role']
    require((role=='source' and facts['unit']=='unit-b') or (role=='recipient' and facts['unit']=='unit-c'),
            'wrong_public_pilot_seed_role')
    require(type(package['files']) is dict and 2<=len(package['files'])<=10,'invalid_public_seed_files')
    files={name:base64.b64decode(value,validate=True) for name,value in package['files'].items()}
    require({'manifest.json','manifest.sig.json'}<=set(files),'public_seed_manifest_missing')
    manifest=ll.manifest(files['manifest.json'],files['manifest.sig.json'],package['trust'],package['manifest_sha256'])
    require(len(manifest['chunks'])<=8,'public_pilot_seed_too_many_chunks')
    expected={'manifest.json','manifest.sig.json'}
    if role=='source':
        expected|={'chunks/'+entry['sha256']+'.json' for entry in manifest['chunks']}
    require(set(files)==expected,'recipient_must_not_receive_management_chunks')
    if role=='source':
        records=[ll.chunk(files['chunks/'+entry['sha256']+'.json'],entry) for entry in manifest['chunks']]
        ll.kp.validate_pack_rules(manifest,records)
    root=Path(state);private(root.lstat(),True)
    public=_directory(root/'public')
    target=public/package['manifest_sha256']
    trust=json.dumps(package['trust'],sort_keys=True).encode()+b'\n'
    if target.exists() or target.is_symlink():
        private(target.lstat(),True)
        require(_read(target/'trust.json',16384)==trust,'public_seed_trust_changed')
        directory=target/('pack' if role=='source' else 'received');private(directory.lstat(),True)
        for name,raw in files.items():require(_read(directory/name,65536)==raw,'existing_public_seed_changed')
    else:
        staging=Path(tempfile.mkdtemp(prefix='.public-seed-',dir=public))
        _write(staging/'trust.json',trust)
        directory=_directory(staging/('pack' if role=='source' else 'received'))
        if role=='source':_directory(directory/'chunks')
        for name,raw in files.items():_write(directory/name,raw)
        os.rename(staging,target)
        fd=os.open(public,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:os.fsync(fd)
        finally:os.close(fd)
    return dict(schema='meshticulum.installed-public-pilot-seed.v1',unit=facts['unit'],role=role,
        manifest_sha256=package['manifest_sha256'],publisher_verified=True,
        files_sha256={name:hashlib.sha256(raw).hexdigest() for name,raw in files.items()},
        recipient_chunks_installed=False,radio_opened=False,socket_opened=False,private_admission=False)
