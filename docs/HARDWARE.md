# Experimental hardware tooling

`firmware/meshticulum-bench/main.cpp` and `protocol.hpp` implement the raw LoRa test coprocessor. Serial and Linux SX1262 HAT host adapters are in `meshticulum.bench`. Installation, inspection, admission, RF completion, peer receipt and application acceptance are separate events.

Build a public **host runtime archive**, without installing or arming a radio:

```sh
python firmware/meshticulum-bench/build_runtime.py --output-dir build/runtime
```

The archive includes canonical Meshticulum packages, an exact source manifest and a hash-verified launcher. Private native services are disabled. A separate root-provisioned finite grant, owner lock, journal and current device evidence are required for dispatch. The explicit `ip-service-run` entry likewise needs a native public IP grant; it opens only its admitted connection and creates no daemon. See [the coordinated hardware pilot](HARDWARE_PILOT.md). `runtime_bootstrap.py` is installation tooling; building an archive grants no device authority.

The retained firmware build and device orchestration scripts are historical lab tooling. Their defaults refer to parent SAGE lab evidence directories and A/B/C roles. A fresh clone does **not** contain that evidence, board support tree, third-party vendor libraries, rollback firmware, private management keys or live grants. These scripts are not a portable firmware provisioning installer. Treat every configured boot, host and artifact pin as requiring fresh verification.

`prepare_build.py` depends on a retained, locally patched MeshCore-derived board support tree and pinned PlatformIO tools. The reconstructed donor is not asserted byte-identical to pristine upstream. RadioLib, MeshCore-derived BSP and platform components retain their own licenses; no third-party source or firmware binary is vendored by this extraction. A reproducible standalone firmware recipe and its license inventory remain on the roadmap.

The host-native firmware harness is in `tests/meshticulum_bench_native`; the tests compile it with the host C++ toolchain and exercise synthetic serial input. It does not qualify real board timing or RF output.
