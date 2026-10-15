"""Typed host/firmware contract and numeric vectors; no native radio authority."""
from dataclasses import replace
from pathlib import Path
import subprocess
import pytest
from meshticulum.bench.gfsk_profile import (GfskProfile,GfskProfileError,RECORD,estimated_airtime_us,sx1262_plan)
from meshticulum.bench.serial_protocol import RadioProfile,SerialError
from meshticulum.bench.serial_worker import SerialWorker
from meshticulum.bench.linux_hat import HardwareError

ROOT=Path(__file__).resolve().parents[1]

def profile(**changes):
 return replace(GfskProfile(921375000,0,90000,256,60000000),**changes)

@pytest.fixture(scope='module')
def native(tmp_path_factory):
 binary=tmp_path_factory.mktemp('gfsk-native')/'vectors'
 subprocess.run(['clang++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-I',str(ROOT/'firmware/meshticulum-bench'),str(ROOT/'tests/gfsk_native/harness.cpp'),'-o',str(binary)],check=True,capture_output=True)
 return binary


def test_distinct_typed_record_host_firmware_and_bit_byte_units(native):
 p=profile();raw=p.encode(board_id=1)
 assert RECORD.size==47 and raw[:3]==b'GF\1' and GfskProfile.decode(raw,board_id=1)==p
 values=subprocess.run([str(native)],input=raw,check=True,capture_output=True).stdout.splitlines()
 assert [int(v) for v in values]==[estimated_airtime_us(p,n) for n in range(1,256)]
 assert estimated_airtime_us(p,255)==17280 and estimated_airtime_us(p,70)==5440
 # The length byte adds64us beyond RadioLib7.7.1's GFSK helper.
 assert estimated_airtime_us(p,255)-(32+64+16+255*8)*8==64


def test_semtech_modulation_and_packet_command_vectors():
 r=sx1262_plan(profile());assert r.packet_type==b'\0'
 assert r.modulation==bytes.fromhex('0020000919008000')
 assert r.packet==bytes.fromhex('002007400001ff0201')
 assert r.sync_word==b'MTGFSK01' and r.crc_initial==b'\xff\xff' and r.crc_polynomial==b'\x10\x21' and r.whitening_initial==0x01ff


def test_public_radiolib_config_order_and_every_fail_stop(native):
 subprocess.run([str(native),'program'],input=profile().encode(board_id=1),check=True,capture_output=True)


@pytest.mark.parametrize('field,value',[('bit_rate_bps',500000),('deviation_hz',50000),('rx_bandwidth_hz',1000000),('preamble_bits',8),('sync_word',b'MT'),('shaping_code',0),('chip_power_dbm',11),('frequency_hz',902000000),('lease_ms',999),('max_boot_attempts',0),('max_boot_estimated_airtime_us',0),('lease_ms',True)])
def test_unreviewed_or_unbounded_profile_refused_by_both_parsers(native,field,value):
 p=profile(**{field:value})
 with pytest.raises(GfskProfileError):p.encode(board_id=1)
 # Types/length errors are rejected by Python before any binary serialization.
 if field in ('lease_ms',) and value is True or field=='sync_word':return
 base=profile();parts=list(RECORD.unpack(base.encode(board_id=1)));indices={'frequency_hz':2,'bit_rate_bps':3,'deviation_hz':4,'rx_bandwidth_hz':5,'chip_power_dbm':6,'preamble_bits':7,'sync_word':8,'shaping_code':9,'lease_ms':10,'max_boot_attempts':11,'max_boot_estimated_airtime_us':12}
 parts[indices[field]]=value;raw=RECORD.pack(*parts)
 assert subprocess.run([str(native)],input=raw,capture_output=True).returncode==2


@pytest.mark.parametrize('size',[0,256,True,1.5])
def test_only_exact_radio_payload_sizes_have_airtime(size):
 with pytest.raises(GfskProfileError):estimated_airtime_us(profile(),size)


def test_lora_class_wire_and_admission_remain_distinct():
 p=RadioProfile(921375000,500000,5,5,0,12,0x42,90000,256,60000000)
 assert type(p) is RadioProfile and len(p.encode(board_id=1))==30
 with pytest.raises(GfskProfileError):estimated_airtime_us(p,255)
 class Channel:pass
 w=SerialWorker(Channel());w.identity=object()
 with pytest.raises(HardwareError,match='invalid_arm_parameters'):
  w.arm(journal=None,profile=profile(),context_digest=bytes(32),authorize=lambda:True,authority_deadline=1)


def test_wrong_record_version_length_or_modem_magic_cannot_downgrade():
 raw=profile().encode(board_id=1)
 for data in [raw[:-1],raw+bytes(1),b'MB'+raw[2:],raw[:2]+b'\2'+raw[3:]]:
  with pytest.raises(GfskProfileError):GfskProfile.decode(data,board_id=1)
