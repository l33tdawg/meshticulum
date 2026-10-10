"""Owned, connected TCP socket with bounded reads and durable byte accounting.

The caller supplies a connected socket from its separately authorized network
setup. Adoption detaches that descriptor from the caller. No listening, DNS,
connect, radio work, or application acceptance occurs in this worker.
"""
import os
import select
import socket
import time

from .authority import IpAuthority
from .journal import IpJournal
from .wire import IpGrant,IpError,FrameKind,MAGIC,HEADER,PREFIX,TAG_BYTES,require


class TcpWorker:
    def __init__(self,connected,grant,local_peer,journal,*,test_rx_cutoff=None):
        require(type(connected) is socket.socket and connected.family in (socket.AF_INET,socket.AF_INET6)
          and connected.type&socket.SOCK_STREAM==socket.SOCK_STREAM,'requires_connected_tcp_socket')
        require(type(grant) is IpGrant and type(journal) is IpJournal and journal.binding==grant.digest,'ip_worker_binding_mismatch')
        self.grant,self.local_peer,self.journal=grant,local_peer,journal
        self.direction=grant.direction(local_peer)
        require(test_rx_cutoff is None or (type(test_rx_cutoff) is int and self.direction==1
                and grant.format=='MR1.response' and 8<=test_rx_cutoff<grant.object_bytes),
                'invalid_explicit_public_tcp_loss_control')
        self.test_rx_cutoff=test_rx_cutoff;self.test_rx_bytes=0;self.test_loss_injected=False
        endpoints=(grant.initiator_address,grant.responder_address)
        self.local,self.remote=endpoints[self.direction],endpoints[1-self.direction]
        require(connected.getsockname()[:2]==self.local and connected.getpeername()[:2]==self.remote,'tcp_endpoint_not_admitted')
        require(journal.owned(), 'ip_journal_not_owned')
        fd=connected.detach();owned=None
        try:
            owned=socket.socket(fileno=fd);owned.setblocking(False)
            owned.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
            info=os.fstat(owned.fileno())
        except BaseException:
            if owned is not None:owned.close()
            else:os.close(fd)
            raise
        self.sock=owned;self.identity=(info.st_dev,info.st_ino)
        self.binding=(grant.digest,local_peer);self.closed=False
        self.buffer=bytearray();self.expected=None

    def owned(self):
        if self.closed or not self.journal.owned():return False
        try:
            info=os.fstat(self.sock.fileno())
            return ((info.st_dev,info.st_ino)==self.identity and self.binding==(self.grant.digest,self.local_peer)
              and self.sock.getsockname()[:2]==self.local and self.sock.getpeername()[:2]==self.remote)
        except OSError:return False

    def _guard(self,authority):
        require(type(authority) is IpAuthority and authority.grant==self.grant and authority.local_peer==self.local_peer,
          'ip_worker_authority_mismatch')
        authority.require_dispatch();require(self.owned(),'ip_worker_not_owned')

    def transmit(self,packet,lane,authority):
        self._guard(authority)
        require(type(packet) is bytes and HEADER.size+TAG_BYTES<len(packet)<=HEADER.size+TAG_BYTES+self.grant.limits.max_plaintext,'invalid_tcp_packet')
        magic,version,kind,direction,session,counter,length=HEADER.unpack(packet[:HEADER.size])
        require(magic==MAGIC and version==1 and direction==self.direction and session==self.grant.session.session_id
                and kind in FrameKind._value2member_map_ and FrameKind(kind).name==lane
                and length==len(packet)-HEADER.size-TAG_BYTES, 'tcp_transmit_lane_binding_mismatch')
        framed=PREFIX.pack(len(packet))+packet
        token=self.journal.reserve(framed,lane);done=0;invoked=False
        stop=min(time.monotonic()+.25,authority.application_deadline_us/1e6)
        try:
            while done<len(framed):
                self._guard(authority)
                if time.monotonic()>=stop:raise IpError('tcp_write_timeout')
                try:
                    invoked=True;count=self.sock.send(memoryview(framed)[done:])
                except BlockingIOError:
                    select.select([], [self.sock], [], min(.01,max(0,stop-time.monotonic())));continue
                require(count>0,'tcp_peer_closed')
                done+=count
                # Failure after a kernel write remains unknown; never send twice.
                self.journal.progress(token,framed,done)
            self._guard(authority)
            self.journal.settle(token,framed,'KERNEL_ACCEPTED')
            return True
        except BaseException:
            try:self.journal.settle(token,framed,'UNKNOWN' if invoked else 'NOT_SENT')
            finally:self.close()
            raise

    def receive(self,authority):
        self._guard(authority)
        try:
            target=PREFIX.size if self.expected is None else PREFIX.size+self.expected
            while len(self.buffer)<target:
                self._guard(authority)
                requested=target-len(self.buffer)
                if self.test_rx_cutoff is not None:requested=min(requested,self.test_rx_cutoff-self.test_rx_bytes)
                maximum=self.journal.reserve_receive(requested)
                try:part=self.sock.recv(maximum)
                except BlockingIOError:
                    self.journal.settle_receive(0);return None
                self.journal.settle_receive(len(part))
                require(bool(part),'tcp_peer_closed')
                self.test_rx_bytes+=len(part)
                if self.test_rx_cutoff is not None and self.test_rx_bytes>=self.test_rx_cutoff:
                    self.test_loss_injected=True
                    # This closes the actual admitted connection after bounded
                    # known reads. Charges persist; no partial object is accepted.
                    raise IpError('explicit_public_pilot_tcp_loss')
                self.buffer.extend(part)
            if self.expected is None:
                self.expected=PREFIX.unpack(self.buffer)[0]
                require(HEADER.size+TAG_BYTES<self.expected<=HEADER.size+TAG_BYTES+self.grant.limits.max_plaintext,'invalid_tcp_frame_length')
                return self.receive(authority)
            packet=bytes(self.buffer[PREFIX.size:]);self.buffer.clear();self.expected=None
            self._guard(authority);return packet
        except BaseException:
            try:self.journal.fence()
            finally:self.close()
            raise

    def close(self):
        if not self.closed:
            self.closed=True;self.buffer.clear();self.expected=None
            self.sock.close()
        # Closing a socket does not refund bytes, establish receipt or close
        # the borrowed journal. Its owner retains the lifetime accounting.
