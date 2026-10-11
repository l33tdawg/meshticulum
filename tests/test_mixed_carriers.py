"""Authenticated modeled RF + actual TCP composition; never physical RF proof."""
from contextlib import ExitStack
import time

import pytest
from meshticulum.contracts.identity import PeerID
from meshticulum.bench.services import ServiceTransfer,PreparedBlockExchange,resource
from meshticulum.resources import lamplighter as ll
from tests.test_ip_transport import pair as tcp_pair,Clock
from tests.test_meshticulum_private_carrier import carrier_pair
from tests.test_meshticulum_resources import pack
from tests.service_helpers import scope


@pytest.mark.parametrize('fault',['none','tcp_disconnect'])
def test_c_to_a_model_radio_to_b_real_tcp_preserves_identity_deadline_and_cost(tmp_path,fault):
    directory,trust,digest,manifest=pack.__wrapped__(tmp_path)
    cache=ll.VerifiedCache(directory,trust,digest);server=resource.BlockServer(cache.read)
    entry=manifest['chunks'][0]
    request=resource.encode_request('/packs/'+digest+'/chunks/'+entry['sha256']+'.json',0,16384)
    reply=server.handle(request);clock=Clock()
    expires=clock()[0]+30000000;deadline=clock()[1]/1e6+31
    with ExitStack()as stack:
        radio_req=stack.enter_context(carrier_pair(tmp_path/'ca-request',request,clock,'unit-c','unit-a',101,
          service=scope('MR1.request',clock,expires)))
        radio_reply=stack.enter_context(carrier_pair(tmp_path/'ac-reply',reply,clock,'unit-a','unit-c',102,
          service=scope('MR1.response',clock,expires)))
        c_peer=radio_req.sender.authority.local_peer;a_peer=radio_req.receiver.authority.local_peer
        b_peer=PeerID(a_peer.chain_id,'b'*64)
        forward,fw,fs,fc=stack.enter_context(tcp_pair(tmp_path/'ab-request',request,initiator=a_peer,responder=b_peer,
          expires=expires,deadline_us=int(deadline*1e6),session_id=b'f'*16,generation=b'g'*16))
        reverse,rw,rs,rc=stack.enter_context(tcp_pair(tmp_path/'ba-reply',reply,'MR1.response',initiator=b_peer,responder=a_peer,
          expires=expires,deadline_us=int(deadline*1e6),session_id=b'h'*16,generation=b'j'*16))
        assert radio_req.sender.cipher.grant.digest==radio_req.receiver.cipher.grant.digest
        assert radio_reply.sender.cipher.grant.digest==radio_reply.receiver.cipher.grant.digest
        c_src,a_radio_rx=ServiceTransfer(radio_req.sender),ServiceTransfer(radio_req.receiver)
        a_tcp_tx,b_tcp_rx=[ServiceTransfer(x)for x in forward]
        b_tcp_tx,a_tcp_rx=[ServiceTransfer(x)for x in reverse]
        a_radio_tx,c_rx=ServiceTransfer(radio_reply.sender),ServiceTransfer(radio_reply.receiver)
        assert a_tcp_tx.authority.local_peer==a_radio_rx.authority.local_peer==a_peer
        assert a_tcp_rx.authority.local_peer==a_radio_tx.authority.local_peer==a_peer
        assert c_rx.authority.local_peer==c_peer
        stages=set()
        def drive():
            radio_req.sender.step();radio_req.receiver.step()
            if radio_req.receiver.flow.complete and 'radio_to_ip' not in stages:
                a_tcp_tx.send(a_radio_rx.received());stages.add('radio_to_ip')
                if fault=='tcp_disconnect':fw[0].close()
            forward[0].step();forward[1].step()
            if forward[1].flow.complete and 'b_response' not in stages:
                b_tcp_tx.send(b_tcp_rx.serve_public(server));stages.add('b_response')
            reverse[0].step();reverse[1].step()
            if reverse[1].flow.complete and 'ip_to_radio' not in stages:
                a_radio_tx.send(a_tcp_rx.received());stages.add('ip_to_radio')
            radio_reply.sender.step();radio_reply.receiver.step()
            time.sleep(.002)
        exchange=PreparedBlockExchange(c_src,c_rx,pump=drive)
        if fault=='tcp_disconnect':
            with pytest.raises(ValueError):exchange(request,len(reply),deadline)
            assert stages=={'radio_to_ip'} and not radio_reply.workers[0].sent
            assert exchange.request.cancelled and exchange.response.cancelled
            assert radio_req.sender.journal.snapshot()['attempts']>0
            assert all(x.authority.application_expires_epoch_us==expires for x in forward+reverse)
            return
        result=exchange(request,len(reply),deadline)
        header=resource.RESPONSE.size;raw=result[header:]
        ll.chunk(raw,entry)
        assert raw==cache.piece(entry['sha256'])
        assert stages=={'radio_to_ip','b_response','ip_to_radio'}
        for x in forward+reverse:
            assert x.authority.application_expires_epoch_us==expires
            assert x.worker.journal.snapshot()['attempts']>0
            assert 'RF_DONE' not in x.worker.journal.snapshot()['outcomes']
        for x in (radio_req.sender,radio_req.receiver,radio_reply.sender,radio_reply.receiver):
            assert x.authority.application_expires_epoch_us==expires
            assert x.journal.snapshot()['reserved_estimated_airtime_us']>0
