"""No-device ioctl fixtures for explicit HAT SPI clock selection."""
import ctypes,stat,struct
from pathlib import Path
from types import SimpleNamespace
import pytest
from meshticulum.bench import linux_hat as h
from tests.test_radio_file_example import example

@pytest.fixture
def hardware(monkeypatch):
 monkeypatch.setattr(h,'os',SimpleNamespace(**{k:getattr(h.os,k) for k in dir(h.os)}))
 trace=[];closed=[];state={'readback':4000000,'dt':1000000,'failure':None}
 monkeypatch.setattr(h.platform,'system',lambda:'Linux');monkeypatch.setattr(h.platform,'machine',lambda:'aarch64');monkeypatch.setattr(h.os,'geteuid',lambda:0)
 def read(path):
  name=str(path)
  if name.endswith('compatible'):return b'rockchip,rk3588-orangepi-5-plus\0'
  if name.endswith('phandle'):return b'abcd'
  if name.endswith('cs-gpios'):return b'abcd'+struct.pack('>II',3,1)
  if name.endswith('spi-max-frequency'):return struct.pack('>I',state['dt'])
  raise AssertionError(name)
 monkeypatch.setattr(Path,'read_bytes',read);monkeypatch.setattr(Path,'read_text',lambda *a,**k:'boot');monkeypatch.setattr(Path,'resolve',lambda self,**kw:Path('/fixture/spi@feb00000/spidev@0'));monkeypatch.setattr(Path,'mkdir',lambda *a,**k:None)
 def info(mode):return SimpleNamespace(st_mode=mode,st_uid=0,st_nlink=1,st_rdev=5)
 monkeypatch.setattr(Path,'lstat',lambda self:info(stat.S_IFCHR|0o600) if str(self)=='/dev/spidev0.0' else info(stat.S_IFDIR|0o700))
 def opening(path,*a):
  trace.append(('open',str(path)))
  return 11 if str(path).endswith('.lock') else 12 if str(path)=='/dev/spidev0.0' else 13
 monkeypatch.setattr(h.os,'open',opening);monkeypatch.setattr(h.os,'fstat',lambda fd:info(stat.S_IFREG|0o600) if fd==11 else info(stat.S_IFCHR|0o600));monkeypatch.setattr(h.os,'close',closed.append);monkeypatch.setattr(h.os,'set_inheritable',lambda *a:None);monkeypatch.setattr(h.os,'set_blocking',lambda *a:None);monkeypatch.setattr(h,'_existing_openers',lambda *a:[]);monkeypatch.setattr(h.fcntl,'flock',lambda *a:None)
 def ioctl(fd,op,value):
  trace.append(('ioctl',op,bytes(value) if isinstance(value,(bytes,bytearray)) else None))
  if state['failure']==op:raise OSError('injected_ioctl')
  if op==h.GPIO_GET_CHIP:value[:]=struct.pack('=32s32sI',b'gpiochip3',b'gpio3',32)
  elif op==h.GPIO_GET_LINES:value.fd=14
  elif op==h.SPI_READ_HZ:value[:]=struct.pack('=I',state['readback'])
  elif op==h.SPI_MESSAGE:
   trace.append(('message',value.speed_hz,value.len,value.bits_per_word,value.cs_change));ctypes.memmove(value.rx_buf,value.tx_buf,value.len);return value.len
  return 0
 monkeypatch.setattr(h.fcntl,'ioctl',ioctl)
 return trace,closed,state

@pytest.mark.parametrize('speed',[None,1000000,4000000])
def test_configuration_and_every_message_match_explicit_speed(hardware,speed):
 trace,closed,state=hardware;t=h.LinuxHAT() if speed is None else h.LinuxHAT(spi_hz=speed);expected=1000000 if speed is None else speed
 assert ('ioctl',h.SPI_WRITE_HZ,struct.pack('=I',expected)) in trace
 assert sum(x[0]=='ioctl' and x[1]==h.SPI_READ_HZ for x in trace)==int(expected==4000000)
 for payload in (b'\xc0\0',bytes(range(256))+b'abcd'):
  assert t.transfer(payload)==payload;assert trace[-1]==('message',expected,len(payload),8,0)
 t.close();assert closed==[14,13,12,11]

@pytest.mark.parametrize('speed',[True,False,0,2000000,16000000,'4000000',None])
def test_invalid_speed_before_any_open(hardware,speed):
 trace,closed,state=hardware
 with pytest.raises(h.HardwareError,match='unsupported_hat_spi_hz'):h.LinuxHAT(spi_hz=speed)
 assert not trace and not closed

@pytest.mark.parametrize('failure',['readback','read_ioctl','write_ioctl','dt'])
def test_refusal_and_cleanup(hardware,failure):
 trace,closed,state=hardware
 if failure=='readback':state['readback']=1000000
 elif failure=='read_ioctl':state['failure']=h.SPI_READ_HZ
 elif failure=='write_ioctl':state['failure']=h.SPI_WRITE_HZ
 else:state['dt']=4000000
 with pytest.raises((h.HardwareError,OSError)):h.LinuxHAT(spi_hz=4000000)
 assert closed==([] if failure=='dt' else [14,13,12,11]);assert not any(x[0]=='message' for x in trace)

@pytest.mark.parametrize('interface,value,valid',[('hat',None,True),('hat',1000000,True),('hat',4000000,True),('usb',None,True),('usb',1000000,False),('usb',4000000,False),('hat',True,False),('hat',8000000,False)])
def test_public_option_is_explicit_hat_only(interface,value,valid):
 args=SimpleNamespace(interface=interface,hat_spi_hz=value)
 if valid:assert example.hat_spi_options(args)==({} if value is None else {'spi_hz':value})
 else:
  with pytest.raises(Exception,match='invalid_hat_spi_hz_option'):example.hat_spi_options(args)

def test_cli_choices_and_file_owner_binding():
 import argparse
 parser=argparse.ArgumentParser();example.add_host_timing_argument(parser)
 assert parser.parse_args([]).hat_spi_hz is None
 for hz in (1000000,4000000):assert parser.parse_args(['--hat-spi-hz',str(hz)]).hat_spi_hz==hz
 with pytest.raises(SystemExit):parser.parse_args(['--hat-spi-hz','8000000'])
 a=SimpleNamespace(hat_spi_hz=1000000);b=SimpleNamespace(hat_spi_hz=4000000)
 assert example.FileHardware._binding(a)!=example.FileHardware._binding(b)
 assert h.SPI_READ_HZ==0x80046b04
