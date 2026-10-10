# Meshticulum

Meshticulum is an experimental transport and resource system for networks that mix LoRa and IP. It gives each transfer explicit peer, service, expiry, queue, credit and medium-budget boundaries. Public resources are checked against signed manifests and content hashes. Private messages use recipient encryption and sender signatures.

It began in SAGE Lantern and now has its own repository. SAGE is an optional application integration; the transport, public-resource and envelope code run without its SDK or services.

## Current state

Implemented source includes the authenticated MT05 flow, durable replay and attempt journals, exclusive serial/SX1262 worker adapters, bounded public MR1 service transfers, resumable verified resources, measured path scheduling and compact private envelopes. The compact envelope preserves the existing encrypted and signed bytes while removing text encoding overhead.

The mixed-path system is still under qualification. Source tests and modeled workers do **not** establish physical RF completion, production private admission or sustained goodput. C→A LoRa→B IP, failover with fresh admission, and matched MeshCore/Reticulum hardware benchmarks remain open. The useful encrypted RF target is 1–1.5 kB/s; it is a target, not a published result.

## Install and test

Python 3.11 or later is required. Radio workers and Linux device entry points require POSIX/Linux hardware interfaces; the offline core runs on the development host.

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
python -m pytest --ignore=tests/integrations
python tools/verify_frozen_flow.py
```

The test suite uses synthetic identities and explicit source models. Local HTTP tests bind loopback only. No test flashes hardware or starts production services.

```python
from meshticulum.contracts.identity import PeerID
from meshticulum.privacy.mesh_binary_envelope import to_binary, to_text
from meshticulum.resources.resource import BlockReader, BlockServer
```

`meshticulum-service --help` and `meshticulum-seed --help` describe explicit entry points. Help and imports grant no radio authority. A service run requires a separately provisioned finite grant and the trusted exclusive worker.

## Project layout

- `src/meshticulum/contracts`, `scheduler`, `flow`: admission, scheduling and reliable flow.
- `src/meshticulum/bench`: experimental authenticated wire, journals, workers and finite service runners.
- `src/meshticulum/resources`: signed public packs, bounded block requests and measured path policy.
- `src/meshticulum/privacy`: offline sealed envelopes and their compact binary carrier.
- `src/meshticulum/integrations/sage`: optional protected inbox, capture and storage adapters.
- `firmware/meshticulum-bench`: raw LoRa coprocessor source and lab tooling.
- `tests`: extracted acceptance and failure controls.

The two small `meshticulum_contracts`/`meshticulum_scheduler` bridges preserve class identity for the accepted, unchanged flow source. Use the canonical `meshticulum.contracts` and `meshticulum.scheduler` imports in new code.

See [extraction and provenance](docs/EXTRACTION.md), [hardware tooling](docs/HARDWARE.md), [SAGE integration](docs/SAGE_INTEGRATION.md) and [remaining qualification](docs/ROADMAP.md). Apache-2.0 licensing and inherited notices are retained.
