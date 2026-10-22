# Experimental post-quantum provider

Meshticulum currently uses classical Ed25519/Curve25519 cryptography. Its encrypted flow uses ChaCha20-Poly1305, and sealed envelopes use PyNaCl SealedBox. The optional source prototype adds X-Wing draft11 key encapsulation, combining ML-KEM-768 with X25519, and requires both Ed25519 and ML-DSA-65 signatures. It does not change the deployed radio or IP protocols. It is not TLS or Noise compatible, and it does not establish a post-quantum native stack or prove forward secrecy.

Run it separately from an installed device environment:

```sh
python3 -m venv .venv-pq
.venv-pq/bin/python -m pip install -e '.[pq-prototype,dev]'
mkdir -p build
curl --fail --location --max-filesize 100000 --max-time 30 \
  --output build/xwing-draft11.txt \
  https://www.ietf.org/archive/id/draft-connolly-cfrg-xwing-kem-11.txt
MESHTICULUM_REQUIRE_PQ=1 MESHTICULUM_XWING_DRAFT_PATH=build/xwing-draft11.txt \
  .venv-pq/bin/python -m pytest tests/test_pq_prototype.py
.venv-pq/bin/python examples/pq_exchange.py
```

The provider pins `cryptography==50.0.2`. Baseline installations keep their existing dependency range and classical behavior; explicitly selecting this provider with a different backend fails instead of falling back. The source controls use the real OpenSSL backend, including all three published draft11 key-generation and decapsulation vectors. They first verify the unmodified draft's SHA256, `1353f61f0d706199d86b6802eab83cabbb5195b2d99fb35bca20f5e4f83db786`, then parse the vectors in memory. A separate, Apache-licensed implementation vector is packaged locally and matches draft11 vector1 exactly. Ordinary baseline tests skip the optional controls; `MESHTICULUM_REQUIRE_PQ=1` makes a missing provider or full-draft input an error.

The caller supplies trusted, pinned dual identity keys. An initiator signs a fresh ephemeral hybrid public key together with both peer identities, the service, object hash, nonce and original expiry. The responder verifies both signatures, encapsulates to that key, signs its reply and confirms the derived secret through AEAD. The initiator verifies both signatures and confirmation, then returns a separate confirmation for the responder. Both independent signatures include the same canonical purpose. Directional data keys are separate from the role-separated confirmation key and nonces.

Identity keys are not accepted through implicit trust on first use. `ReplayWindow` refuses reused peer nonces and stops after 256 entries; it is an in-process control, not persistent native replay protection. Replay and exchange states have one owning PID and thread; foreign-thread and forked-process transitions refuse. The caller must preserve replay state, enforce revocation and admission, and bind the expiry to its existing original deadline. The prototype validates caller-supplied epoch observations; it does not replace native monotonic clocks, boot bindings or revocation checks. Python memory deletion does not prove key erasure. Native key storage, protected admission, transport integration, persistent replay, key rotation and post-quantum forward-secrecy qualification remain separate work.

The runnable demo encrypts and verifies 512 actual public bytes once using a fresh session nonce and transcript AAD. Its cached-identity handshake has these exact binary lengths:

| Message | Bytes | Calculated fragments at 193 bytes |
| --- | ---: | ---: |
| Signed ephemeral offer | 4,751 | 25 |
| Signed reply and responder confirmation | 4,547 | 24 |
| Initiator confirmation | 54 | 1 |
| Total | 9,352 | 50 |

This is a framing calculation, excluding radio headers, ACKs, retries, PHY preambles and identity distribution. Each dual identity public key is 1,984 bytes; each dual signature is 3,373 bytes. Public-key distribution needs its own trusted protocol and budget. Signing each radio fragment would add excessive overhead; this candidate signs whole exchanges and offers a separate whole-manifest signing API. No radio airtime, network speed or native security improvement has been measured for it.

The underlying standards are [NIST FIPS203](https://csrc.nist.gov/pubs/fips/203/final) and [FIPS204](https://csrc.nist.gov/pubs/fips/204/final). Their current potential-update sheets include ML-KEM explanatory corrections and ML-DSA notation, bounds and signing-loop corrections; this prototype delegates those algorithms to the maintained backend rather than implementing their arithmetic. Algorithm use does not imply FIPS140 module validation. [RFC10024](https://www.rfc-editor.org/rfc/rfc10024.html) specifies a TLS hybrid and explicitly depends on the TLS transcript; its construction is not transplanted here. The exact generic KEM is [X-Wing draft11](https://www.ietf.org/archive/id/draft-connolly-cfrg-xwing-kem-11.txt), whose draft status is part of the pinned suite.

The [cryptography project](https://cryptography.io/en/stable/installation/) documents ARM64 macOS and Linux support. The isolated source experiment ran on macOS ARM64 with cryptography50.0.2 and OpenSSL4.0.3; Linux device compatibility is not thereby qualified. Cryptography is dual Apache2/BSD licensed; its bundled OpenSSL uses Apache2. The prototype is Apache2. Its packaged vector comes from the [X-Wing-KEM-Team's licensed implementation](https://github.com/X-Wing-KEM-Team/xwing/blob/706fe95dfff656323c9bc4bbebb55342c8495c51/LICENSE), with precise source attribution in the test notice. The full unmodified Independent Submission draft is a separately fetched test input; no automatic IETF code-component BSD grant is asserted. The provider requires independent cryptographic review before protocol adoption.
