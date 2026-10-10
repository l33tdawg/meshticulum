"""Signed source installation versus manifest-only recipient boundaries."""
import base64
import json

import pytest
from meshticulum.bench.pilot_seed import install
from meshticulum.bench.wire import WireError
from meshticulum.resources import lamplighter as ll
from tests.test_ip_node import facts
from tests.test_meshticulum_resources import pack


def package(pack,unit,role):
    files={name:(pack[0]/name).read_bytes() for name in ('manifest.json','manifest.sig.json')}
    if role=='source':
        for entry in pack[3]['chunks']:
            name='chunks/'+entry['sha256']+'.json';files[name]=(pack[0]/name).read_bytes()
    return dict(schema='meshticulum.pilot-public-seed.v1',unit=unit,host_boot_id=facts(unit)['host_boot_id'],
        role=role,manifest_sha256=pack[2],trust=pack[1],files={name:base64.b64encode(raw).decode() for name,raw in files.items()})


def test_source_is_verified_and_recipient_gets_no_management_chunks(pack,tmp_path):
    for unit,role in [('unit-b','source'),('unit-c','recipient')]:
        root=tmp_path/unit;root.mkdir(mode=0o700)
        value=package(pack,unit,role)
        result=install(value,state=root,facts=facts(unit))
        assert result['publisher_verified'] and not result['recipient_chunks_installed'] and not result['radio_opened']
        directory=root/'public'/pack[2]/('pack' if role=='source' else 'received')
        assert len(list((directory/'chunks').glob('*.json')))==8 if role=='source' else not (directory/'chunks').exists()
        assert install(value,state=root,facts=facts(unit))==result
        if role=='source':assert ll.VerifiedCache(directory,pack[1],pack[2]).piece(pack[3]['chunks'][0]['sha256'])


@pytest.mark.parametrize('fault',['recipient_data','publisher','hash','boot','role'])
def test_invalid_seed_does_not_create_a_native_public_store(pack,tmp_path,fault):
    root=tmp_path/'state';root.mkdir(mode=0o700)
    value=package(pack,'unit-c','recipient')
    if fault=='recipient_data':
        entry=pack[3]['chunks'][0];name='chunks/'+entry['sha256']+'.json'
        value['files'][name]=base64.b64encode((pack[0]/name).read_bytes()).decode()
    if fault=='publisher':value['trust']['keys'][0]['status']='revoked'
    if fault=='hash':value['manifest_sha256']='f'*64
    if fault=='boot':value['host_boot_id']='31f9c178-6090-40d9-9359-ecf1c7b950b1'
    if fault=='role':value['role']='source'
    with pytest.raises(ValueError):install(value,state=root,facts=facts('unit-c'))
    assert not (root/'public').exists()
