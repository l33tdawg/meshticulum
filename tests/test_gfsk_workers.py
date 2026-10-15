"""GFSK transport/MCU/SQLite controls with synthetic channels, no physical RF."""
import hashlib,math,struct
from contextlib import closing
from dataclasses import replace
import pytest
from meshticulum.bench.gfsk_profile import GfskProfile,estimated_airtime_us,sx1262_plan
from meshticulum.bench.serial_protocol import Decoder,Frame,Message,Outcome,GfskRawReceive
from meshticulum.bench.serial_worker import GfskSerialWorker,journal_binding
from meshticulum.bench.attempts import AttemptJournal
from meshticulum.bench.hat_worker import GfskHATWorker,journal_binding as hat_binding
from meshticulum.bench.gfsk_sx1262 import GfskSX1262
from meshticulum.bench.linux_hat import HardwareError
from tests.test_meshticulum_bench_worker import BOOT,VERSION,Channel
from tests.test_meshticulum_bench_sx1262 import Chip

P=GfskProfile(921375000,0,60000,10,10000000)

class GfskChannel(Channel):
 def write(self,data,deadline):
  f,=Decoder().feed(data)
  if f.type is Message.GFSK_ARM:
   self.writes.append(f);self.offer(Frame(Message.GFSK_ARM_REPLY,BOOT,f.request,f.body));return
  if f.type is not Message.TRANSMIT:return super().write(data,deadline)
  self.writes.append(f)
  if self.fault=='partial':raise OSError('synthetic write failure')
  estimate=estimated_airtime_us(P,len(f.body))+(1 if self.fault=='estimate' else 0)
  packet=hashlib.sha256(f.body).digest()
  for outcome in [Outcome.STARTED,Outcome.RF_DONE]:
   body=struct.pack('!B32sQQIh',outcome,packet,estimate,0 if outcome is Outcome.STARTED else estimate,0 if outcome is Outcome.STARTED else 1,0)
   self.offer(Frame(Message.TX_RESULT,BOOT,f.request,body))


@pytest.mark.parametrize('fault',[None,'partial','estimate'])
def test_serial_gfsk_reserves_before_write_and_fences_unknown_without_refund(tmp_path,fault):
 tmp_path.chmod(0o700);channel=GfskChannel(fault);worker=GfskSerialWorker(channel)
 identity=worker.discover(expected_board_id=1,expected_version=VERSION)
 with closing(AttemptJournal(tmp_path/'attempts.sqlite',binding=journal_binding(b'x'*32,identity,P),max_attempts=10,max_airtime_us=10000000,create=True,request_clock=lambda:100000000)) as journal:
  worker.arm(journal=journal,profile=P,context_digest=b'x'*32,authorize=lambda:True,authority_deadline=200)
  assert channel.writes[-1].type is Message.GFSK_ARM and len(channel.writes[-1].body)==47
  packet=bytes(range(255));result=worker.transmit(packet);snapshot=journal.snapshot()
  assert snapshot['attempts']==1 and snapshot['reserved_estimated_airtime_us']==math.ceil(17280*1.1)+1000
  assert result.outcome==('RF_DONE' if fault is None else 'UNKNOWN')
  assert worker.fenced is (fault is not None)


def test_gfsk_receive_has_distinct_message_and_no_snr():
 f=Frame(Message.GFSK_RECEIVE,BOOT,0,struct.pack('!h',-925)+b'ciphertext')
 r=GfskRawReceive.from_frame(f,boot=BOOT);assert r.rssi_dbm==-92.5 and r.snr_db is None and r.packet==b'ciphertext'


class GfskChip(Chip):
 def transfer(self,data):
  result=bytearray(super().transfer(data))
  if data[0]==0x14:result[2:]=bytes([2,142,140])
  return bytes(result)


def test_spi_gfsk_configuration_rearm_receive_and_exact_airtime():
 chip=GfskChip();radio=GfskSX1262(chip);radio.configure(P);plan=sx1262_plan(P)
 assert b'\x8b'+plan.modulation in chip.commands and b'\x8c'+plan.packet in chip.commands
 assert chip.packet_type==0 and bytes(chip.reg[x] for x in range(0x06C0,0x06C8))==b'MTGFSK01'
 radio.start_receive(authority_deadline=160);chip.flags=2;received=radio.poll_receive()
 assert received.packet==chip.rx and received.snr_db is None
 assert radio.start_transmit(b'packet',authority_deadline=160,antenna_confirmed=True) is True
 assert radio.tx_estimate==estimated_airtime_us(P,6)
 chip.time+=.01;assert radio.poll_transmit().outcome is Outcome.RF_DONE


def test_gfsk_hat_retains_original_worker_authority_and_paid_debt(tmp_path):
 tmp_path.chmod(0o700);chip=GfskChip()
 with closing(AttemptJournal(tmp_path/'hat.sqlite',binding=hat_binding(b'x'*32,chip.boot_id,P),max_attempts=10,max_airtime_us=10000000,create=True,request_clock=lambda:100)) as journal:
  worker=GfskHATWorker(chip,journal=journal,profile=P,context_digest=b'x'*32,authorize=lambda:True,authority_deadline=160,antenna_confirmed=True,monotonic=chip.monotonic,time_basis='model-monotonic')
  result=worker.transmit(b'packet');assert result.outcome=='RF_DONE' and journal.snapshot()['attempts']==1
  worker.deadline=170;chip.time=160
  with pytest.raises(HardwareError):worker.transmit(b'expired')
  assert journal.snapshot()['attempts']==1
  worker.disarm()


def test_gfsk_hat_estimator_mutation_from_authorize_refuses_before_new_reservation(tmp_path):
 tmp_path.chmod(0o700);chip=GfskChip();attack=[False];holder={}
 def authorize():
  if attack[0]:holder['worker']._airtime=lambda p,n:1
  return True
 with closing(AttemptJournal(tmp_path/'mutation.sqlite',binding=hat_binding(b'y'*32,chip.boot_id,P),max_attempts=10,max_airtime_us=10000000,create=True,request_clock=lambda:100)) as journal:
  worker=GfskHATWorker(chip,journal=journal,profile=P,context_digest=b'y'*32,authorize=authorize,authority_deadline=160,antenna_confirmed=True,monotonic=chip.monotonic,time_basis='model-monotonic');holder['worker']=worker
  assert worker.transmit(b'first').outcome=='RF_DONE';before=journal.snapshot();attack[0]=True
  with pytest.raises(HardwareError,match='hat_owner_or_clock_changed'):worker.transmit(bytes(255))
  assert journal.snapshot()==before and chip.tx_calls==1
  worker.disarm()


def test_gfsk_serial_dispatch_mutation_refuses_without_spending_or_writing(tmp_path):
 tmp_path.chmod(0o700);channel=GfskChannel();worker=GfskSerialWorker(channel);identity=worker.discover(expected_board_id=1,expected_version=VERSION)
 with closing(AttemptJournal(tmp_path/'serial-mutation.sqlite',binding=journal_binding(b'z'*32,identity,P),max_attempts=10,max_airtime_us=10000000,create=True,request_clock=lambda:100000000)) as journal:
  worker.arm(journal=journal,profile=P,context_digest=b'z'*32,authorize=lambda:True,authority_deadline=200)
  before=journal.snapshot();writes=len(channel.writes);worker._airtime=lambda p,n:1
  with pytest.raises(HardwareError,match='serial_modem_dispatch_changed'):worker.transmit(bytes(255))
  assert journal.snapshot()==before and len(channel.writes)==writes


@pytest.mark.parametrize('hook',['_rx_tail_us','_receive_evidence','_packet'])
def test_gfsk_driver_modem_hook_replacement_refuses_before_more_spi(hook):
 chip=GfskChip();radio=GfskSX1262(chip);radio.configure(P);before=len(chip.commands)
 setattr(radio,hook,lambda *args:0)
 with pytest.raises(HardwareError,match='sx1262_transport_changed'):radio.start_receive(authority_deadline=160)
 assert len(chip.commands)==before
