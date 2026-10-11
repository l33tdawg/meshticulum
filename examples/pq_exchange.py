# SPDX-License-Identifier: Apache-2.0
"""Real-backend, in-memory PQ prototype demo; no hardware or network access."""
import hashlib
import json
import secrets
import time

from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from meshticulum.privacy.pq_prototype import (
    BACKEND_VERSION, SUITE, Context, DualIdentity, OfferState, ReplyState, ReplayWindow)


def main():
    # Public deterministic actual bytes; no compression or title padding.
    payload = b''.join(hashlib.sha256(b'Meshticulum public PQ demo'+i.to_bytes(8,'big')).digest()
                       for i in range(16))
    alice, bob = DualIdentity(), DualIdentity()
    manifest = json.dumps(dict(bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest(),
                               source='public deterministic source-only fixture'),
                          sort_keys=True, separators=(',',':')).encode()
    manifest_signature = alice.sign(b'manifest', manifest)
    alice.public.verify(b'manifest', manifest, manifest_signature)
    issued = time.time_ns()//1000
    context = Context(alice.public.identifier, bob.public.identifier, b'MR1.request',
        hashlib.sha256(payload).digest(), issued, issued+60_000_000, secrets.token_bytes(32))
    offer = OfferState(alice, context, issued)
    reply = ReplyState(bob, offer.packet, context, alice.public, time.time_ns()//1000,
                       ReplayWindow(bob.public.identifier))
    sender, confirmation = offer.finish(reply.packet, bob.public, time.time_ns()//1000)
    recipient = reply.finish(confirmation, time.time_ns()//1000)
    context.live(time.time_ns()//1000)
    nonce = secrets.token_bytes(12)  # One encryption in this fresh session.
    aad = SUITE+b'/demo-data\0'+sender.transcript_sha256+context.encode()
    ciphertext = ChaCha20Poly1305(sender.outgoing).encrypt(nonce,payload,aad)
    decoded = ChaCha20Poly1305(recipient.incoming).decrypt(nonce,ciphertext,aad)
    assert decoded == payload and hashlib.sha256(decoded).digest() == context.object_sha256
    context.live(time.time_ns()//1000)
    packets = [offer.packet, reply.packet, confirmation]
    print(json.dumps(dict(schema='meshticulum.source-pq-prototype-demo.v1',
        backend=BACKEND_VERSION, suite=SUITE.decode(), dual_signatures_required=True,
        mutual_confirmation=True, actual_decoded_bytes=len(decoded),
        actual_decoded_sha256=hashlib.sha256(decoded).hexdigest(),
        application_ciphertext_bytes=len(ciphertext), application_nonce_bytes=len(nonce),
        handshake_packet_bytes=list(map(len,packets)), handshake_bytes=sum(map(len,packets)),
        calculated_193_byte_fragments=sum((len(p)+192)//193 for p in packets),
        framing_excludes=['radio headers','ACKs','retries','identity key distribution','PHY preamble'],
        native_qualification=False, secure_erasure_proven=False, deployed_pfs_proven=False),
        sort_keys=True))


if __name__=='__main__': main()
