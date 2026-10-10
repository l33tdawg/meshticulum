# Hardware library and deployment boundary

Meshticulum owns reusable radio protocol and driver code. [Lantern](https://github.com/l33tdawg/sage-lantern) owns device integration: Linux jobs, native provisioning, fleet configuration, runtime packaging, installation, firmware flashing, coordinated pilots and rollback. The dependency is **Lantern → Meshticulum**. Importing a Meshticulum driver does not start those application services or acquire their authority.

## What remains in Meshticulum

[`main.cpp`](../firmware/meshticulum-bench/main.cpp) and [`protocol.hpp`](../firmware/meshticulum-bench/protocol.hpp) implement the project-authored raw LoRa test coprocessor and its bounded serial protocol. The library provides the [serial worker](../src/meshticulum/bench/serial_worker.py), [SX1262 driver](../src/meshticulum/bench/sx1262.py), [Linux HAT adapter](../src/meshticulum/bench/linux_hat.py) and [HAT worker](../src/meshticulum/bench/hat_worker.py). These consume caller-owned ports, journals, clocks and current authority. The host application selects its device and owns setup, finite leases, containment and cleanup.

Installation, admission, local RF completion, authenticated peer receipt, durable bytes and application acceptance remain separate events. `RF_DONE` requires matching driver evidence for the original boot, request and packet; it does not establish remote storage or publisher verification. Unknown attempts retain their reservations and fence further dispatch. New grants and process cleanup cannot refund old debt.

The [host-native protocol harness](../tests/meshticulum_bench_native) compiles against synthetic Arduino/ESP32 headers and exercises bounded serial input. [`check_linux_abi.c`](../firmware/meshticulum-bench/check_linux_abi.c) supports checking the Linux device ABI. These controls do not qualify a real board's timing, antenna, regional configuration or RF output.

## Board support and a portable build

The coprocessor requires explicitly supplied compatible board support, Arduino/ESP32 platform components and RadioLib. A portable recipe must identify those inputs and the toolchain, rather than locate a retained Lantern lab directory or silently reuse a patched vendor tree. Source and artifact hashes, compile settings, board identity and license inventory belong with the resulting build record. Supplying those inputs is distinct from permission to flash or operate a device.

The earlier lab firmware build used a locally patched MeshCore-derived board-support tree. Its [historical build preparation](https://github.com/l33tdawg/sage-lantern/blob/codex/meshticulum-boundary/firmware/meshticulum-bench/prepare_build.py) belongs to Lantern. It is not asserted byte-identical to pristine MeshCore and does not make this protocol wire-compatible with MeshCore. A new standalone recipe must retain that distinction and must not relabel historical firmware hashes as a fresh build.

RadioLib, MeshCore-derived board support and platform components keep their upstream licenses. The Meshticulum source license does not relicense those inputs. Reproducible board builds, third-party attribution and the release review remain open publication gates; a host-native stub test cannot close them.

## Where to run Lantern jobs

The actual finite [radio node and service entry](https://github.com/l33tdawg/sage-lantern/tree/main/software/meshticulum-integration/src/lantern_meshticulum/bench), [TCP entry](https://github.com/l33tdawg/sage-lantern/blob/codex/meshticulum-boundary/software/meshticulum-integration/src/lantern_meshticulum/ip/node.py) and pilot controller live in Lantern's `software/meshticulum-integration`. Its [firmware/runtime tooling](https://github.com/l33tdawg/sage-lantern/tree/main/firmware/meshticulum-bench) owns bundle builds, provisioning, staging, flashing and rollback. Meshticulum does not retain copies of those launchers or deployment scripts.

Lantern supplies current host/boot/service facts, independent adjacent-link keys, original resource deadlines and its native owner before dispatch. A finite radio job needs an exclusive worker, a separately provisioned grant and bounded disarm. A finite TCP job must authorize socket setup and independently bind the actual endpoints. Building or installing their runtime bundle grants neither radio nor connection authority.

The planned C→A LoRa→B IP pilot, fresh LoRa admission after IP loss, verified-piece resume and measured return to IP are Lantern integration work using Meshticulum primitives. They still require a fresh coordinated device window, actual process/driver evidence and bounded cleanup. Preserve production services, identities, protected stores and raw failures. The earlier physical [radio benchmark](PERFORMANCE.md) remains bound to its own runtime and settings; it does not qualify a newly built bundle or the full mixed path.
