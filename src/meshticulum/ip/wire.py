"""Authenticated IP frames with explicit peer/service/object/transport binding.

Pairwise keys are independently provisioned, not discovered from a socket or
claimed by a Python record. This source mode supplies no forward secrecy or
native application admission. Radio framing/key domains are unchanged.
"""
from dataclasses import dataclass, field, asdict
from enum import IntEnum
import hashlib
import ipaddress
import json
import struct

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from ..contracts.identity import PeerID, SessionKey, ServiceKind
from ..bench.wire import WireError

DOMAIN=b'meshticulum.ip.v1\x00'
MAGIC=b'MI'
HEADER=struct.Struct('!2sBBB16sQH')
PREFIX=struct.Struct('!I')
TAG_BYTES=16
MAX_PACKET=4096
FORMATS={'MR1.request':ServiceKind.KNOWLEDGE,'MR1.response':ServiceKind.KNOWLEDGE,'LME.binary':ServiceKind.MESSAGE,'MPF2.record':ServiceKind.MESSAGE}


class IpError(WireError):
    pass


def require(ok,reason):
    if not ok:raise IpError(reason)


def integer(value,lo,hi,reason):
    require(type(value) is int and lo<=value<=hi,reason)


def address(value):
    require(type(value) is tuple and len(value)==2 and type(value[0]) is str,'invalid_ip_endpoint')
    try: ip=ipaddress.ip_address(value[0])
    except ValueError: raise IpError('invalid_ip_endpoint') from None
    require(str(ip)==value[0],'noncanonical_ip_endpoint')
    integer(value[1],1,65535,'invalid_ip_endpoint')
    return value


class FrameKind(IntEnum):
    DATA=1
    ACK=2


@dataclass(frozen=True,slots=True)
class IpLimits:
    max_plaintext:int=2048
    max_frames:int=2048
    max_tx_stream_bytes:int=1048576
    max_rx_stream_bytes:int=1048576
    max_object_bytes:int=65536
    window_frames:int=8
    max_retries:int=6

    def __post_init__(self):
        integer(self.max_plaintext,23,MAX_PACKET-HEADER.size-TAG_BYTES,'invalid_ip_plaintext_limit')
        integer(self.max_frames,1,65535,'invalid_ip_frame_limit')
        for n in (self.max_tx_stream_bytes,self.max_rx_stream_bytes):integer(n,128,64*1024*1024,'invalid_ip_stream_limit')
        integer(self.max_object_bytes,self.max_plaintext,1024*1024,'invalid_ip_object_limit')
        integer(self.window_frames,1,32,'invalid_ip_window')
        integer(self.max_retries,1,16,'invalid_ip_retry_limit')


@dataclass(frozen=True,slots=True)
class IpGrant:
    session:SessionKey
    format:str
    object_bytes:int
    object_sha256:bytes
    route_binding_digest:bytes
    issued_epoch_us:int
    expires_epoch_us:int
    application_expires_epoch_us:int
    initiator_address:tuple
    responder_address:tuple
    limits:IpLimits
    secret:bytes=field(repr=False)

    def __post_init__(self):
        require(type(self.session) is SessionKey and type(self.limits) is IpLimits,'invalid_ip_session')
        require(type(self.format) is str and self.format in FORMATS,'invalid_ip_service')
        integer(self.object_bytes,1,self.limits.max_object_bytes,'invalid_ip_object')
        for value in (self.object_sha256,self.route_binding_digest,self.secret):
            require(type(value) is bytes and len(value)==32,'invalid_ip_binding')
        for value in (self.issued_epoch_us,self.expires_epoch_us,self.application_expires_epoch_us):integer(value,1,2**63-1,'invalid_ip_expiry')
        require(self.issued_epoch_us<self.application_expires_epoch_us<=self.expires_epoch_us
                and self.expires_epoch_us-self.issued_epoch_us<=240000000,'invalid_ip_expiry')
        address(self.initiator_address);address(self.responder_address)
        require(self.initiator_address!=self.responder_address,'same_ip_endpoint')

    @property
    def service_kind(self):return FORMATS[self.format]

    @property
    def max_frames(self):return self.limits.max_frames

    def direction(self,peer):
        require(type(peer) is PeerID,'invalid_ip_peer')
        if peer==self.session.initiator:return 0
        require(peer==self.session.responder,'peer_not_in_ip_session')
        return 1

    def context(self):
        return json.dumps({'protocol':'meshticulum.ip.v1','transport':'tcp',
          'initiator':asdict(self.session.initiator),'responder':asdict(self.session.responder),
          'session_id':self.session.session_id.hex(),'session_generation':self.session.session_generation.hex(),
          'service_kind':self.service_kind.value,'format':self.format,'object_bytes':self.object_bytes,
          'object_sha256':self.object_sha256.hex(),'route_binding_digest':self.route_binding_digest.hex(),
          'issued_epoch_us':self.issued_epoch_us,'expires_epoch_us':self.expires_epoch_us,
          'application_expires_epoch_us':self.application_expires_epoch_us,
          'initiator_address':self.initiator_address,'responder_address':self.responder_address,
          'limits':asdict(self.limits)},sort_keys=True,separators=(',',':')).encode('ascii')

    @property
    def digest(self):return hashlib.sha256(self.context()).digest()


@dataclass(frozen=True,slots=True)
class VerifiedFrame:
    kind:FrameKind
    counter:int
    payload:bytes=field(repr=False)


class IpCipher:
    def __init__(self,grant,local_peer,store):
        require(type(grant) is IpGrant,'invalid_ip_grant')
        self.grant,self.direction,self.store=grant,grant.direction(local_peer),store
        require(store.binding==(grant.digest,self.direction),'ip_state_binding_mismatch')
        keys=[HKDF(algorithm=hashes.SHA256(),length=32,salt=grant.session.session_generation,
            info=DOMAIN+bytes([d])+grant.context()).derive(grant.secret) for d in (0,1)]
        self._tx=ChaCha20Poly1305(keys[self.direction]);self._rx=ChaCha20Poly1305(keys[1-self.direction])

    def seal(self,payload,kind,*,authorize):
        require(type(payload) is bytes and 0<len(payload)<=self.grant.limits.max_plaintext,'invalid_ip_payload')
        require(type(kind) is FrameKind,'invalid_ip_frame_kind')
        counter=self.store.reserve_transmit(authorize=authorize)
        header=HEADER.pack(MAGIC,1,int(kind),self.direction,self.grant.session.session_id,counter,len(payload))
        return header+self._tx.encrypt(bytes(4)+counter.to_bytes(8,'big'),payload,DOMAIN+self.grant.digest+header)

    def open(self,packet,*,authorize):
        require(type(packet) is bytes and HEADER.size+TAG_BYTES<len(packet)<=HEADER.size+TAG_BYTES+self.grant.limits.max_plaintext,'invalid_ip_packet')
        header=packet[:HEADER.size]
        magic,version,kind,direction,session,counter,length=HEADER.unpack(header)
        require(magic==MAGIC and version==1,'unsupported_ip_wire')
        require(direction==1-self.direction and session==self.grant.session.session_id,'wrong_ip_session_direction')
        integer(counter,1,self.grant.max_frames,'invalid_ip_counter')
        require(kind in FrameKind._value2member_map_ and length==len(packet)-HEADER.size-TAG_BYTES,'invalid_ip_header')
        self.store.check_live(authorize=authorize)
        try:payload=self._rx.decrypt(bytes(4)+counter.to_bytes(8,'big'),packet[HEADER.size:],DOMAIN+self.grant.digest+header)
        except InvalidTag:raise IpError('ip_authentication_failed') from None
        self.store.record_receive(counter,authorize=authorize)
        return VerifiedFrame(FrameKind(kind),counter,payload)
