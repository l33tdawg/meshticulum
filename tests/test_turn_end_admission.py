"""Source-only capability/AEAD foundation; runtime turn sealing tested separately."""
import importlib.util
import argparse,json
from types import SimpleNamespace
from pathlib import Path
import pytest
from meshticulum.bench.turn_end import admitted, capability
from meshticulum.bench.burst_protocol import policy_for_train32
from meshticulum.bench.wire import SessionCipher,FrameKind,HEADER,WireError
spec=importlib.util.spec_from_file_location('turn_end_wire_fixture',Path(__file__).with_name('test_meshticulum_bench_wire.py'))
fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)
setup,A,B,ALLOW,grant=fixture.setup,fixture.A,fixture.B,fixture.ALLOW,fixture.grant

def plan():
    return dict(schema='meshticulum.bench-run.v1',data_burst=policy_for_train32(),
                unit='unit-a',sender_unit='unit-a',profile=dict(max_boot_attempts=1024,max_boot_estimated_airtime_us=300000000),
                operation_limits={r:{'attempts':56,'estimated_airtime_us':3000000} for r in ('sender','receiver')},
                turn_end_capability=capability())

def test_strict_capability_and_default():
    assert not admitted({})
    assert admitted(plan())
    for key,value in [('maximum_train_frames',32.0),('host_pending_limit',True),
                      ('schema','unknown'),('seal_rule','predict-later')]:
        p=plan();p['turn_end_capability'][key]=value
        with pytest.raises(WireError):admitted(p)
    for key in ('data_burst','operation_limits'):
        p=plan();del p[key]
        with pytest.raises(WireError):admitted(p)
    p=plan();p['schema']='meshticulum.service-run.v1'
    with pytest.raises(WireError):admitted(p)

def opted(cipher):
    return SessionCipher(cipher.grant,A if cipher.direction==0 else B,cipher.store,turn_end=True)

def test_marker_same_geometry_authenticated_replay_protected(setup):
    _,_,endpoint=setup
    spec=grant(max_plaintext=208)
    a=opted(endpoint('a',A,spec));b=opted(endpoint('b',B,spec))
    packet=a.seal(b'x'*208,FrameKind.DATA_TURN_END,authorize=ALLOW)
    assert len(packet)==255
    changed=bytearray(packet);changed[3]=int(FrameKind.DATA)
    with pytest.raises(WireError,match='authentication_failed'):b.open(bytes(changed),authorize=ALLOW)
    value=b.open(packet,authorize=ALLOW)
    assert value.kind is FrameKind.DATA_TURN_END and value.payload==b'x'*208
    with pytest.raises(WireError,match='replayed'):b.open(packet,authorize=ALLOW)

def test_default_cannot_send_or_receive_marker(setup):
    _,_,endpoint=setup
    a=endpoint('a',A);b=endpoint('b',B)
    with pytest.raises(WireError,match='not_admitted'):a.seal(b'data',FrameKind.DATA_TURN_END,authorize=ALLOW)
    packet=opted(a).seal(b'data',FrameKind.DATA_TURN_END,authorize=ALLOW)
    with pytest.raises(WireError,match='not_admitted'):b.open(packet,authorize=ALLOW)

@pytest.mark.parametrize('kind',[FrameKind.DATA,FrameKind.ACK,FrameKind.CONTROL])
def test_existing_kind_bytes_identical_with_optional_codec_enabled(setup,kind):
    _,_,endpoint=setup
    a=endpoint('a',A);other=opted(endpoint('other',A))
    assert a.seal(b'body',kind,authorize=ALLOW)==other.seal(b'body',kind,authorize=ALLOW)

@pytest.mark.parametrize('value',[1,0,None,'true'])
def test_cipher_option_strict_bool(setup,value):
    _,_,endpoint=setup;a=endpoint('a',A)
    with pytest.raises(WireError,match='invalid_turn_end_cipher_option'):
        SessionCipher(a.grant,A,a.store,turn_end=value)

def test_public_cli_default_and_grant_mismatch_before_hardware():
    import radio_file
    parser=argparse.ArgumentParser();radio_file.add_host_timing_argument(parser)
    assert parser.parse_args([]).data_turn_end is False
    assert parser.parse_args(['--data-turn-end']).data_turn_end is True
    for enabled,p in [(True,{}),(False,plan()),(1,{})]:
        with pytest.raises(WireError,match='turn_end_example_capability_mismatch'):
            radio_file.run_object(SimpleNamespace(data_turn_end=enabled),json.dumps(p).encode(),
                                  None,None,None,None,None)
