# Post-quantum status

Meshticulum's default radio, TCP and private-envelope protocols use classical cryptography. An opt-in, test-only hybrid post-quantum source prototype is available in this repository for evaluation. It is not integrated into or deployed with those protocols.

## What the prototype adds

| Layer | Experimental choice | Purpose |
| --- | --- | --- |
| Key establishment | X-Wing draft 11: X25519 + ML-KEM-768 | Combine classical and post-quantum shared secrets |
| Peer authentication | Both Ed25519 and ML-DSA-65 signatures required | Bind the exchange to independently pinned peers |
| Payload encryption | ChaCha20-Poly1305 with directional session keys | Encrypt and authenticate data after the exchange |

The exchange binds both peers, the service, object hash, nonce and original expiry. Both sides confirm the derived secret. Explicitly selecting the experimental provider fails if its backend is unavailable; it does not silently fall back to classical key establishment.

ML-KEM and ML-DSA are standardized in [NIST FIPS 203](https://csrc.nist.gov/pubs/fips/203/final) and [FIPS 204](https://csrc.nist.gov/pubs/fips/204/final). The generic hybrid construction is pinned to [X-Wing draft 11](https://www.ietf.org/archive/id/draft-connolly-cfrg-xwing-kem-11.txt), which remains a draft. Using these algorithms does not imply that Meshticulum or its provider is a FIPS-validated cryptographic module.

The aim is to protect future sessions against attackers who record encrypted traffic now and attempt to recover its keys with a future quantum computer. That requires post-quantum key establishment and appropriate authentication; the current classical protocols do not provide that guarantee.

## What we have checked

The real-backend source prototype passes 49 focused checks, including all three published X-Wing draft-11 vectors. Its local demo encrypts, decrypts and verifies 512 actual bytes.

These are source results. Independent cryptographic review, persistent replay protection, protected key storage and rotation, native transport integration, and device performance remain open. Python object deletion does not prove secure key erasure or deployed forward secrecy.

## The cost on a radio link

With identity keys already pinned, the demo's complete handshake is **9,352 bytes**: a 4,751-byte offer, a 4,547-byte reply and a 54-byte confirmation. That is **50 calculated fragments** at 193 useful bytes per fragment, before radio headers, ACKs, retries, preambles and identity distribution. Each dual public identity adds 1,984 bytes when distributed.

The design performs the large asymmetric exchange when establishing a session and uses compact symmetric encryption for its data. It does not attach a post-quantum signature to every radio fragment. We still need to measure setup latency, loss recovery, memory use and useful throughput on devices before choosing a default. No PQ radio throughput result is available yet.

## Try it or share its status

Use the [prototype instructions and demo](POST_QUANTUM_PROTOTYPE.md) in a separate development environment. A default installation keeps the classical behavior; enabling the optional extra does not integrate the provider into the radio or IP transports.

You can describe the project this way:

> Meshticulum currently uses classical cryptography. We have an opt-in, tested experimental hybrid post-quantum source prototype combining X25519 + ML-KEM-768 and Ed25519 + ML-DSA-65. It has not been integrated into or deployed with the radio stack.
