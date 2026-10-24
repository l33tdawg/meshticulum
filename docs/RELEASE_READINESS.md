# Meshticulum alpha release status

## 1.0.0-alpha.2 candidate — 2026-10-24

**Prepared for review; not published.** The next candidate uses Python package
version **`1.0.0a2`** and proposed Git tag **`v1.0.0-alpha.2`**. The published
`v1.0.0-alpha` / `1.0.0a1` release and its checksummed assets remain unchanged.

The candidate adds opt-in host-backed MCU DATA queueing, request-aware serial
event routing, durable physical-result recovery/drain, queue-aware retry timing,
USB bundle sending, modem-aware LoRa admission, permanent-airtime-exhaustion
diagnostics and separately bounded operation journals. Keys, authenticated ACKs,
credit and retry decisions remain on the host. Frozen flow source remains pinned.
See [MCU acceleration](MCU_ACCELERATION.md) and the [changelog](../CHANGELOG.md).

The [24 October sanitized comparison](../benchmarks/2026-10-24-mcu-queue-lora/README.md)
records one fixed-order pair at an operator-reported approximately 20 m on
SF7/BW500. Both transfers verified 8,164 useful bytes. Sender startup through
authenticated receipt improved from **8.766668 s / 931.255 B/s** synchronous to
**7.526076 s / 1,084.762 B/s** queued, a **16.484%** goodput gain. All 46 queued
DATA packets physically completed and were released; checked endpoint completion
followed authenticated receipt by 27.456 ms. Both modes had zero retries, receive
drops or new UNKNOWN outcomes, with unchanged boots and service lifetimes.
This one pair does not establish sustained capacity, wider recovery, field range
or an upstream performance/security ranking.

That radio measurement used source `f06ebeb3e687da4688657311830e95326a0107d2`,
wheel SHA-256 `7998a705ab6a5641ecb9191f08e9b63ba7a676612dcfa7455e291e6177149ad0`
and `mtb1-6cfde1fdd4` firmware. Its metadata still said `1.0.0a1`; it is not the
17 October published wheel. The version-rebuilt alpha.2 wheel needs its own
clean-install/content checks. Release correspondence must identify the 70
unchanged runtime files and the sole `__version__` literal change separately;
this is not fresh RF execution of the rebuilt wheel. Historical MCU020 and
installed-alpha1 proofs retain their original identities and scope.

The proposed distribution remains **selection A: SDK and firmware source build**.
It includes a reproducible wheel and normalized source archive, source-build
guide, project license/notices, current scoped proofs, candidate validation,
manifest and checksums. It includes no prebuilt MCU BIN/ELF or third-party wheels.
The [6cf build record](../provenance/MCU_BURST_FIRMWARE.json) retains its two
same-host Heltec build scope; the separate comparison supplies installed/radio
evidence. Its historical `flashed: false` and `rf_qualified: false` flags are not
rewritten. MCU020 notice inventories and two-target proofs do not establish a
new 6cf full linked-notice audit, vendor-source reconstruction or Xiao physical
acceptance. Optional prebuilt firmware remains a separate distribution review.

Before publication, review the exact candidate commit, Python 3.11–3.13/PQ CI,
frozen-flow/boundary/docs/website/benchmark controls, independent artifact
reproduction, clean installation, measured-code correspondence, selected archive
contents/notices and SHA-256 manifest. The release's exact source and selected
asset hashes belong in its final `RELEASE_MANIFEST.json` and `SHA256SUMS`.
No release build creates device or RF authority.

## Retained 1.0.0-alpha publication — 2026-10-17

**Status on 2026-10-17: published as an experimental GitHub prerelease.** [`v1.0.0-alpha`](https://github.com/l33tdawg/meshticulum/releases/tag/v1.0.0-alpha) points to source `673eb60d24dc60f67371e24bc5c9caeec3a68508`, and package version `1.0.0a1`. The release provides the tested SDK wheel, normalized source archive, firmware source-build guide, notices, proof records, manifest and checksums. The source retains its published development history. [Repository history](https://github.com/l33tdawg/meshticulum/blob/main/docs/REPOSITORY_HISTORY.md) explains the separation. Any consuming Lantern pair retains its own dependency and qualification records.

The [17 October public SDK radio samples](RADIO_SDK_MEASUREMENTS.md) completed two fresh five-part 100,000-byte files at **2.03 and 1.95 kB/s (16.24 and 15.59 kbit/s)** of useful application throughput, including sender startup through the final authenticated receipt. Full scope-to-closure rates were **1.86 and 1.76 kB/s (14.85 and 14.05 kbit/s)**. All ten receiver confirmations were accepted and all twenty endpoint children exited successfully; the files had zero and one DATA retransmissions, with zero new receiver drops. A separate fresh 23,900-byte file reached **2.53 kB/s (20.28 kbit/s)** through receipt. All used near-bench GFSK250 at approximately one metre. This initial repeat does not establish sustained capacity, wider deployment or new native/private qualification. A separate clean installed-wheel run at release source `673eb60` completed a fresh 100 kB file at **1.74 kB/s (13.92 kbit/s) including startup and a five-second pause after verified part 0**, or **1.61 kB/s (12.91 kbit/s) through endpoint closure**. All five confirmations and all ten child exits passed, with publisher and whole-file verification. [The installed-SDK record](../provenance/PUBLIC_INSTALLED_SDK_PROOF.json) binds both normal virtual environments, 67 runtime files per host and continuation under the unchanged original window. This closes that finite installation/verified-piece continuation check for the published source-build alpha.

The [18 October larger-file samples](RADIO_SDK_MEASUREMENTS.md#larger-public-files) extended the same public path to verified **250 kB, 500 kB and 1 MB** files at **2.111, 2.216 and 2.230 kB/s (16.889, 17.725 and 17.838 kbit/s)** including sender startup, with independent whole-file verification. The 1 MB figure is a CPU-isolated repeat; the original host-contended 1.750 kB/s sample remains in the record. These post-alpha checks use source `1f85608`; they do not move the immutable alpha tag or substitute for a future release review.

## What the alpha includes

The published alpha is an installable experimental transport/resource package with reviewed boundaries, reproducible artifacts and explicit limitations. It includes the frozen MT05 flow, reusable bounded radio workers, authenticated TCP source prototype, MR1 public-resource interfaces, verified-piece resume and offline recipient-encrypted envelopes.

The published alpha provides a deployable SDK and compatible firmware build path, with reliable, independently verified completion and useful encrypted goodput **above 1 kB/s (8 kbit/s) including sender startup** on the declared near-bench GFSK250 path, within the original deadlines. The report keeps DATA-only, sender-start, request-to-verification and whole-operation time separate, retaining setup, ACK/retry, verification and cleanup costs. A short sample above the floor, raw modem bitrate or source-model pass does not establish sustained capacity, field range or another installation. The broader **1 to 1.5 kB/s (8 to 12 kbit/s)** programme goal and optional **4 to 5 kB/s (32 to 40 kbit/s)** stretch after alpha remain in the [roadmap](ROADMAP.md).

[SAGE Lantern](https://github.com/l33tdawg/sage-lantern) owns private SAGE adapters, protected capture and policy, native Linux node/pilot entry points, provisioning, runtime bundles and device/fleet deployment. Those implementations are not distributed in the Meshticulum package. Lantern consumes an explicitly pinned Meshticulum dependency; Meshticulum does not import Lantern or require its SDK.

The TCP prototype has loopback controls, and Lantern has modeled native entry-point controls. A separate finite native campaign at historical pins core `6605ba714ce414445673b40bfcfe93fe467b86aa`, Lantern [a41851a](https://github.com/l33tdawg/sage-lantern/commit/a41851a4301008bd840a8508187a59a150eeaa0e) and runtime `d3194a9` completed a public C→A LoRa→B IP request and reverse reply with **977 independently verified public bytes**. [The native milestone record](IP_TRANSPORT.md#recorded-native-public-milestone) gives the full identities and scope; [Lantern’s pilot guide and evidence](https://github.com/l33tdawg/sage-lantern/blob/c596664b41e6ae1bd37cf64591fb7bd827533900/docs/lantern/MESHTICULUM_PUBLIC_PILOT.md#evidence-and-cleanup) retain the operator boundary. Earlier radio goodput measurements remain bound to their older runtime.

A separate fresh failure/resume campaign observed TCP loss after **512 stream bytes** and preserved its first verified piece. It refused fresh LoRa **before ARM/TX** because a fixed **90-second lease** exceeded approximately **85 seconds remaining**. That earlier attempt did not complete resume. Its lease-sizing fix was source-tested and undeployed at that checkpoint; the failure remains historical at the pins above.

A later fresh native campaign at core `a89c1ca6fdf1865d61367cf3a8d59ac1ab50cb39`, Lantern runtime code [379025e](https://github.com/l33tdawg/sage-lantern/commit/379025e177619ee3ffd51ef8d5ae0b2bc27dd54e) and runtime `856570c` completed loss, fresh C↔B LoRa request/reply and measured IP return within **one original 160-second window**. It rejected the partial reply after an injected **512-stream-byte loss**, preserved the first verified piece and independently verified **four 977-byte pieces / 3,908 useful bytes** before the unchanged expiry. Ten sessions ended with **twenty terminal, retired native children** and **three terminal owned management masters**. The read-only journal/holder/service audit passed and handback completed before **11:35 UTC on 2026-10-10**. [Full execution pins and scope](IP_TRANSPORT.md#recorded-native-public-resume-and-ip-return) · [Lantern evidence](https://github.com/l33tdawg/sage-lantern/blob/2f96d9029f972e665286209d9883418d431ab1d0/docs/lantern/evidence/meshticulum-native-resume-20261010.json).

The tiny service samples—**54.349727 seconds** for the resumed LoRa piece and **9.529958 seconds** for fresh IP—include management preparation and final radio ACK windows. They establish the campaign’s current path choice, not sustained carrier capacity or a matched upstream benchmark. Private production and measured RF airtime/energy/EIRP remain open. Current firmware correspondence and the selected distribution have their own review below; these historical path samples do not establish alpha acceptance.

An alpha does not promise stable wire/API compatibility, autonomous routing, a MeshCore/Reticulum packet gateway or production private dispatch. It also does not complete the active programme. Wider hardware, private-backend and benchmark qualifications remain in [ROADMAP.md](ROADMAP.md).

## Published distribution: SDK and firmware source build

The published **selection A** contains the SDK wheel and source distribution, project `LICENSE`/`NOTICE`, the public firmware recipe and its retained notices. Recipients fetch the fourteen pinned vendor inputs directly from their publishers and build locally. No compiled firmware is bundled in these SDK packages. Package-content and notice review passed for that selection before publication.

The [retained MCU020 proof](../provenance/PUBLIC_MCU020_PROOF.json), bound to candidate `673eb60`, matches the current four application files, recipe and input lock to independently prepared application BIN/ELF outputs for both named targets on the recorded macOS-arm64 host. This closes the application recipe-to-image check through those locked vendor inputs. The retained Heltec installation record checks its written application span by ROM MD5; later runs are linked by runtime-tag continuity after a boot change. Those records do not supply fresh full-device SHA-256 readback, attest how every vendor precompiled SDK library was originally built, prove a new installation, or transfer physical acceptance to the Xiao target. [Hardware provenance](HARDWARE.md#board-support-and-a-portable-build) retains these distinctions.

**Selection B**, optional prebuilt development firmware, is a separate distribution decision. Its binary/source/object/relink/notice companions are prepared for review; complete notice closure and legal compliance are not asserted. Review and publish that selection only if it is included. Its unresolved vendor-source identities remain recorded even when selection A is chosen. No SDK source-build release implies approval of the optional binaries.

## Published version and artifacts

Python version **`1.0.0a1`** is published at Git tag **`v1.0.0-alpha`** on source `673eb60d24dc60f67371e24bc5c9caeec3a68508`. The GitHub prerelease title is **`Meshticulum 1.0.0-alpha`**. The release includes the exact selected files and hashes below.

The published assets include:

- A wheel and source distribution built from the candidate commit, with SHA-256 hashes, dependency versions, Apache-2.0 license and retained notices. Check installation and canonical imports independently of the SAGE parent checkout.
- A reviewed Meshticulum distribution manifest containing reusable core code and required support files. Exclude Lantern/SAGE adapters, native deployment tools, protected data and product configuration. Any public-only host-runtime archive used for qualification is a separately owned Lantern artifact, with its own exact dependency, source manifest and launcher hash; building it grants no device or dispatch authority.
- Candidate validation records: standalone tests, frozen-flow verification, documentation/benchmark checks, Python CI matrix, clean package installation and supported-radio completion/recovery records. Any advertised Lantern integration needs its own exact compatible-SDK/native records. Record skips, failures, toolchain versions and scope alongside hashes.
- Release notes stating experimental behavior, supported entry points, original-deadline/replay/debt semantics, known limitations and retained benchmark conditions. Link every claimed result to evidence; keep unavailable measurements null.
- Firmware preparation/build records from the [explicit primary-input recipe](FIRMWARE_RECIPE.md), tied to the exact input lock, patch, recipe, source manifest and target output hashes. Preserve failed attempts and identify the macOS-arm64 host/CLI environment. The retained current-application reproduction has its stated same-host scope. Build records alone do not imply flashing, native acceptance or complete vendor-source correspondence.

For source-archive repeatability, retain the original backend outputs and normalize their timestamps/owner metadata with [`normalize_sdist.py`](../tools/normalize_sdist.py), using an explicit `--source-date-epoch` fixed to the candidate commit. The tool preserves file bytes and permissions, rejects duplicate/unsafe paths and refuses to overwrite an existing output. Compare two independently built normalized archives and inspect their member manifests. A raw backend archive can vary in timestamps even when its content matches; retain that failed byte-comparison alongside the normalized result. This packaging step establishes no native or release acceptance.

The [extraction validation record](../provenance/VALIDATION.json) is a historical checkpoint, not proof that a later candidate passed. The current Meshticulum artifact and any separately qualified Lantern integration require their own records. Moving application code cannot carry qualification forward automatically.

## Release review and retained qualification checklist

The selected-artifact review covered the exact release tree; the open item below is retained programme qualification rather than a claim reversed by publication.

- [x] Review covers the exact Meshticulum candidate, one-way dependency, excluded application files and selected artifact manifest. Any consuming Lantern integration still needs its own pins and native qualification.
- [x] Standalone tests and the Python 3.11–3.13 [CI matrix](../.github/workflows/tests.yml) passed for the release source and the documentation head with unchanged runtime, firmware, examples and packaging. [`verify_frozen_flow.py`](../tools/verify_frozen_flow.py), [`check_docs.py`](../tools/check_docs.py) and [`verify_benchmark_report.py`](../tools/verify_benchmark_report.py) remain release controls.
- [x] The standalone SDK installs without Lantern or the SAGE SDK, and its public radio example completes and reopens verified work on the declared GFSK250 HAT-to-Heltec path. The [installed-wheel check](../provenance/PUBLIC_INSTALLED_SDK_PROOF.json) retains the original 240-second window, settled journals, disarm and all ten terminal children. Its pause occurs after a completed piece; in-flight crash recovery and private/native application qualification remain separate programme requirements.
- [x] The Meshticulum wheel and source distribution reproduce their recorded manifests, contain required support files and preserve frozen-flow pins. Any separately distributed Lantern host bundle still needs independent evidence.
- [ ] Expiry, boot binding, revocation, unknown writes and lifetime debt remain intact through cancellation, restart and fresh child admissions. Public quarantine cannot become a verified piece without request, manifest and publisher checks.
- [x] Documentation distinguishes kernel acceptance, RF completion, authenticated peer receipt, durable bytes and application acceptance. The current GFSK samples, historical LoRa results and cited upstream figures remain within their own workloads and timing conditions; 4 to 5 kB/s (32 to 40 kbit/s) remains a later stretch.
- [x] The source-build selection retains the checked recipe, archive/patch/license hashes, named targets and current reproduction records. Optional prebuilt firmware retains its separate binary/source/object/relink/notice review.

## Retained programme requirements

Earlier extraction commits retain Lantern implementations as development history. The owner approved retaining that published history; rewriting it into a new initial history is not an alpha prerequisite. [Repository history](https://github.com/l33tdawg/meshticulum/blob/main/docs/REPOSITORY_HISTORY.md) and the [boundary record](../provenance/REPOSITORY_BOUNDARY.json) explain the separation. The release review checked its exact source, selected package contents and retained branch/tag boundary; current package boundaries do not erase or qualify historical contents.

Lantern owns native execution and deployment. The public mixed exchange and later successful loss/resume/IP-return campaign are recorded above; the earlier lease refusal remains a separate failed attempt. Further native runs require a fresh coordinated device window, bounded cleanup and independent recipient publisher verification. Each campaign must retain debt, reuse only verified pieces under the original deadline and use fresh verified timing when selecting an IP return. A completed finite campaign creates no reusable dispatch authority.

Private production still requires qualified native owner/consent/revocation and private transport, including encrypted-backend admission. The owner selected comparison with published MeshCore/Reticulum figures; running counterpart benchmark campaigns is not required. Review each cited figure’s hardware, settings, privacy, completion semantics and request/response/ACK/retry scope, keeping missing conditions and incomparable quantities explicit. Sustained useful RF goodput and latency remain separate qualification requirements; actual RF airtime, energy and EIRP remain unmeasured.

The [firmware recipe](FIRMWARE_RECIPE.md) now specifies 14 primary archive inputs, isolated preparation and two named targets. The HWCDC patch retains its file's Apache-2.0 grant; overall LGPL-2.1-or-later framework text and SDK/per-file exceptions are separate. The [validation record](../provenance/FIRMWARE_RECIPE_VALIDATION.json) binds the final corrected recipe/lock to independent fresh reproduction of binary/ELF/bootloader/partitions for both targets in runs 05/06 on one pinned macOS-arm64 host; maps differ. Earlier copied-dependency controls produced different binaries. This closes the stated one-host build-provenance check. Those historical outputs have their own application identity. The retained MCU020 checks above establish current application BIN/ELF reproduction through the locked vendor inputs; neither record establishes new native qualification or full vendor-source construction.

The [linked-notice audit](FIRMWARE_LINKED_NOTICES.md) retains the earlier allocated-section inventories and notice questions. Current recipient preparation adds selected source/object/relink/notice companions, while exact identities for four original Xtensa HAL sources and complete vendor SDK/compiler build reconstruction remain unproved. These limits remain relevant to optional binary distribution and full firmware provenance. Retained WiFi/Bluetooth object contributions do not establish active services or native qualification.

Target-bound build evidence, the applicable source/notice obligations and release review remain required for each future distribution and paired Lantern hardware qualification. Selection A passed its package-content/notice review; selection B remains separate and optional. The raw-radio application tag is not the whole firmware identity. Keep the complete [roadmap](ROADMAP.md) and its private/native requirements; the public SDK alpha does not mark that programme complete.

The released wheel has a clean-install and supported-radio verified-piece continuation record at source `673eb60`. The earlier incomplete installed trial remains in that record. This finite pause/continuation result does not establish in-flight crash recovery or wider hardware coverage. Lantern retains the exact Meshticulum, integration and runtime identities for its campaigns, and private owner/consent/transport qualification remains open for private production. Wider sustained-capacity and latency evidence remains programme work. Claims against published upstream figures require their documented scope.

The owner approved publication after reviewing the exact source, hashes and validation scope. See the [release notes](../CHANGELOG.md), the [GitHub prerelease](https://github.com/l33tdawg/meshticulum/releases/tag/v1.0.0-alpha) and the [remaining roadmap](ROADMAP.md).
