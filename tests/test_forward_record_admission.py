# SPDX-License-Identifier: Apache-2.0
"""Generic private record admission; RF/native/application policy are models."""
import pytest
from meshticulum.privacy.forward_session import (
    inspect_record, ForwardSessionError, HEADER, MAGIC, H1,H2,CONFIRM_I,CONFIRM_R,DATA)
from meshticulum.bench.services import _shape, ServiceTransfer
from meshticulum.bench.service_runner import ServiceRunner
from meshticulum.bench.wire import WireError
from meshticulum.contracts.identity import ServiceKind
from meshticulum.bench.authority import SERVICE_FORMATS
from meshticulum.ip.wire import FORMATS
from tests.test_forward_session import pair, ready, envelope
from tests.test_meshticulum_bench_runtime import Clock
from tests.test_meshticulum_private_carrier import carrier_pair
from tests.service_helpers import scope


def packets(tmp_path):
    p=pair(tmp_path);messages=ready(p)
    return (*messages,p[0].seal(envelope(p)))


def test_actual_five_records_have_explicit_private_shape_and_header(tmp_path):
    values=packets(tmp_path)
    for packet,kind,counter in zip(values,(H1,H2,CONFIRM_I,CONFIRM_R,DATA),(0,0,1,1,2)):
        actual,session,number=inspect_record(packet)
        assert actual==kind and len(session)==16 and number==counter
        assert _shape(packet,'MPF2.record') is None
        with pytest.raises(Exception):_shape(packet,'LME.binary')
    assert SERVICE_FORMATS['MPF2.record'] is FORMATS['MPF2.record'] is ServiceKind.MESSAGE


@pytest.mark.parametrize('packet',[
    HEADER.pack(b'MPF1',H1,b'a'*16,0)+b'x'*48,
    HEADER.pack(MAGIC,H1,bytes(16),0)+b'x'*48,
    HEADER.pack(MAGIC,H1,b'a'*16,1)+b'x'*48,
    HEADER.pack(MAGIC,H2,b'a'*16,0)+b'x'*47,
    HEADER.pack(MAGIC,CONFIRM_I,b'a'*16,0)+b'x'*112,
    HEADER.pack(MAGIC,CONFIRM_R,b'a'*16,1)+b'x'*111,
    HEADER.pack(MAGIC,DATA,b'a'*16,1)+b'x'*100,
    HEADER.pack(MAGIC,DATA,b'a'*16,4097)+b'x'*100,
    HEADER.pack(MAGIC,DATA,b'a'*16,2)+b'x'*67,
    HEADER.pack(MAGIC,6,b'a'*16,2)+b'x'*100,
    b'x'*2145,bytearray(b'x'*77)])
def test_invalid_record_shape_cannot_be_admitted_as_private_object(packet):
    with pytest.raises(ForwardSessionError):inspect_record(packet)
    with pytest.raises(ForwardSessionError):_shape(packet,'MPF2.record')


@pytest.mark.parametrize('index',range(5))
def test_each_exact_record_binds_service_transfer_but_public_runner_refuses_before_dispatch(tmp_path,index):
    packet=packets(tmp_path)[index];clock=Clock()
    with carrier_pair(tmp_path/'carrier',packet,clock,'unit-a','unit-b',index+1,
                      service=dict(format='MPF2.record',kind='protected_message',application_expires_epoch_us=clock()[0]+60000000)) as p:
        sender=ServiceTransfer(p.sender)
        assert sender.kind is ServiceKind.MESSAGE and sender.format=='MPF2.record'
        with pytest.raises(WireError):ServiceRunner(sender,payload=packet)
        assert not any(worker.sent for worker in p.workers)
        before=p.sender.journal.snapshot()
        changed=packet[:-1]+bytes([packet[-1]^1])
        with pytest.raises(WireError):sender.send(changed)
        assert p.sender.journal.snapshot()==before
        assert not any(worker.sent for worker in p.workers)


def test_shape_does_not_authenticate_ciphertext(tmp_path):
    packet=packets(tmp_path)[4]
    tampered=packet[:-1]+bytes([packet[-1]^1])
    assert inspect_record(tampered)==inspect_record(packet)
    # Service hash and endpoint AEAD/signatures must still independently reject.
