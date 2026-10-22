# Contributing to Meshticulum

Meshticulum is an experimental public alpha. Reproducible bug reports,
documentation corrections and small, well-scoped source changes are welcome.
You do not need radio hardware to contribute.

## Choose a path

| I want to help with | Start here |
| --- | --- |
| Test the SDK or firmware | [Testers' Guide](docs/TESTER_GUIDE.md) and the [community tester campaign](https://github.com/l33tdawg/meshticulum/issues/43) |
| Report a result or problem | [Open a tester report](https://github.com/l33tdawg/meshticulum/issues/new?template=tester-report.yml) |
| Work on an offline backend contract | [Issue #44](https://github.com/l33tdawg/meshticulum/issues/44) |
| Improve source, tests or documentation | Open or comment on a focused issue, then prepare a small pull request |

For a radio result, state the exact revision, hardware, profile, topology,
timing boundary and cleanup outcome. Separate build, installation, RF
completion, authenticated peer receipt, content verification and application
acceptance. A build or source test does not establish a physical result.

Never publish private keys, shared secrets, credentials, full device serial
numbers or precise locations. Do not transmit on a frequency or at a power
until you have made the applicable antenna and local regulatory decision.

## Set up the source tree

Python 3.11 or later is supported. CI currently exercises Python 3.11, 3.12
and 3.13.

```sh
git clone https://github.com/l33tdawg/meshticulum.git
cd meshticulum
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
```

Keep imports in the public `meshticulum.*` namespace. The dependency direction
is **Lantern → Meshticulum**; private SAGE adapters, product provisioning,
protected storage and fleet deployment belong in the separate
[SAGE Lantern](https://github.com/l33tdawg/sage-lantern) repository.

## Check a change

Run the standalone suite and the documentation controls before opening a pull
request:

```sh
python -m pytest
python tools/verify_frozen_flow.py
python tools/check_docs.py
python tools/check_website.py
```

Add targeted tests for behavior changes and preserve failures, retries,
unknown outcomes and charged attempt/airtime debt rather than hiding them.
Hardware work needs its own current finite authorization and exclusive device
ownership; a pull request cannot grant RF authority.

## Pull requests

Use a short branch name and keep each pull request to one reviewable change.
Explain what changed, why it is needed, the commands run and any remaining
qualification limit. Link the issue or tester report when one exists.

`src/meshticulum/flow/flow.py` and `flow_port.py` are the accepted frozen
candidate. Changing them requires an explicitly reviewed flow revision. Read
[AGENTS.md](AGENTS.md) before changing transport behavior or repository
boundaries.

Documentation examples must use the same isolated interpreter and pinned
inputs as the real workflow. Prefer exact commands over “run the script.”
