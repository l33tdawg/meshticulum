"""Native descriptor/lifecycle controls, with actual loopback sockets and locks."""
import os
import threading

import pytest
from meshticulum.ip.wire import IpError
from meshticulum.bench.wire import WireError
from meshticulum.ip.journal import IpJournal
from meshticulum.bench.services import ServiceTransfer,resource
from meshticulum.bench.service_runner import ServiceRunner
from tests.test_ip_transport import pair


def payload():return resource.encode_request('/packs/'+'a'*64+'/manifest.json',0,32)


def test_lost_journal_owner_descriptor_stops_every_send_lane(tmp_path):
    with pair(tmp_path/'lost-owner',payload())as(flows,workers,stores,clock):
        journal=workers[0].journal;fd=journal.lock_fd
        # Native cleanup lost the lock descriptor; do not let final cleanup
        # accidentally close a subsequently reused descriptor number.
        os.close(fd)
        try:
            assert journal.owned() is False
            before=journal.db.execute('SELECT count(*) FROM attempts').fetchone()[0]
            runner=ServiceRunner(ServiceTransfer(flows[0]),payload=payload())
            with pytest.raises(WireError):runner.start()
            assert journal.db.execute('SELECT count(*) FROM attempts').fetchone()[0]==before
        finally:journal.lock_fd=None


def test_authority_close_waits_for_the_held_lifecycle(tmp_path):
    with pair(tmp_path/'close-authority',payload())as(flows,workers,stores,clock):
        authority=flows[0].authority;entered=threading.Event();done=threading.Event()
        def close():
            entered.set();authority.close();done.set()
        with authority.application_lease():
            thread=threading.Thread(target=close);thread.start()
            assert entered.wait(1)
            assert not done.wait(.03)
            authority.require_dispatch()
        thread.join(timeout=2)
        assert done.is_set() and not thread.is_alive()
        assert authority.closed


def test_same_thread_cannot_close_a_held_authority(tmp_path):
    with pair(tmp_path/'nested-close',payload())as(flows,workers,stores,clock):
        authority=flows[0].authority
        with authority.application_lease():
            with pytest.raises(IpError):authority.close()
            authority.require_dispatch()
        assert not authority.closed


def test_journal_close_waits_for_commit_and_does_not_erase_debt(tmp_path):
    with pair(tmp_path/'close-journal',payload())as(flows,workers,stores,clock):
        journal=workers[0].journal;entered=threading.Event();done=threading.Event()
        saved_path=journal.path;saved_grant=journal.grant
        journal.reserve(b'reserved sealed frame','DATA')
        def close():
            entered.set();journal.close();done.set()
        with journal.transaction():
            thread=threading.Thread(target=close);thread.start()
            assert entered.wait(1)
            assert not done.wait(.03)
        thread.join(timeout=2)
        assert done.is_set() and not thread.is_alive()
        reopened=IpJournal(saved_path,saved_grant)
        try:
            assert reopened.snapshot()['outcomes']=={'UNKNOWN':1}
            assert reopened.snapshot()['fenced']
        finally:reopened.close()


def test_reused_owner_fd_is_not_owned_and_not_closed_by_cleanup(tmp_path):
    with pair(tmp_path/'reused-owner',payload())as(flows,workers,stores,clock):
        journal=workers[0].journal;fd=journal.lock_fd
        os.close(fd)
        other=os.open(tmp_path/'unrelated',os.O_CREAT|os.O_RDWR,0o600)
        if other!=fd:os.dup2(other,fd);os.close(other)
        try:
            assert not journal.owned()
            journal.close()
            # Cleanup must not close someone else's descriptor just because
            # its number is the old journal-owner number.
            os.fstat(fd)
        finally:os.close(fd)


def test_same_thread_journal_close_refused_until_transaction_completes(tmp_path):
    with pair(tmp_path/'transaction-close',payload())as(flows,workers,stores,clock):
        journal=workers[0].journal
        with journal.transaction():
            with pytest.raises(IpError):journal.close()
            assert journal.db.execute('SELECT count(*) FROM attempts').fetchone()[0]==0
        assert journal.owned()
