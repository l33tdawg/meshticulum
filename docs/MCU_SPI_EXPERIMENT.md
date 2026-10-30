# Optional Heltec SPI clock experiment

The firmware recipe supports an explicit SPI clock experiment for Heltec V4. Both pinned targets built successfully, and one finite v5 Heltec comparison transfer completed at 8 MHz. That sample recorded lower useful-file goodput with more DATA loss; it establishes no throughput gain or repeated reliability qualification.

`prepare --heltec-spi-hz 8000000` selects 8 MHz for Heltec's radio SPI transactions. Explicit `2000000` provides a 2 MHz control through the same mechanism. Omission preserves the locked upstream 2 MHz behavior, the exact existing generated header, its application identity and the existing `build_options` shape. Native profiling remains independently selected with `--native-profile`; the SPI option does not silently enable it.

For a separately reviewed preparation, the option accompanies the existing arguments:

```sh
build/firmware-cli/bin/python firmware/meshticulum-bench/prepare_firmware.py prepare \
  --archive-dir build/firmware-archives \
  --output-dir build/firmware-spi-experiment \
  --native-profile --heltec-spi-hz 8000000
```

The recipe accepts only exact integers 2,000,000 and 8,000,000. It appends the selection to the existing force-included `native_profile_config.hpp`, so the application inventory remains eight inputs. Explicit 2 MHz and 8 MHz each have an identity distinct from omission. `SOURCE.json` records `heltec_spi_hz` only when explicitly selected; the build verifier reconstructs the exact header and requires its forced inclusion for both target environments before invoking PlatformIO.

The generated clause overrides RadioLib's public `RADIOLIB_DEFAULT_SPI_SETTINGS` only under the pinned `HELTEC_LORA_V4` macro and board ID 1. It supplies `SPISettings(selected_hz, MSBFIRST, SPI_MODE0)` to the existing Module constructor default. XIAO's `SEEED_XIAO_S3` / board ID 2 retains the upstream 2 MHz setting. Because both target builds share the generated application inventory, XIAO's application identity changes for an explicit option even though its radio clock does not. Unknown or contradictory target macros, mismatched board IDs and external definitions of the SPI setting are compile-time errors for the explicit experiment.

No board source, RadioLib HAL, radio application code, IRQ ordering, RX restart, queue depth, 5 ms / 100 ms listening floor, lease, attempt limit, airtime accounting or serial pacing is changed. The existing constructor API is used; there is no out-of-band `setFrequency`, mutable HAL replacement or ninth application input. Original input archives and the lock remain unchanged.

Source controls verify default byte identity, the exact option/header/identity binding, strict invalid-value refusal before output creation, target selection and macro conflicts with a host C++ constructor spy. The spy exercises the constructor-default convention; it does not compile the radio driver or qualify a board. Drifted metadata, header, identity or missing forced include is refused before PlatformIO. The native controls remain source evidence, separate from the physical result below.

A 255-byte packet's 257-byte WriteBuffer SPI exchange has calculated wire times of 1,028 µs at 2 MHz and 257 µs at 8 MHz. That 771 µs difference is only ideal serial-clock arithmetic. The byte-at-a-time HAL, command setup, BUSY polling, USB work, radio airtime, loss recovery and other overhead remain. Do not infer a driver-call or useful-file speedup from this calculation.

## Recorded build and physical sample

Source `3988fce58a64172f335ea68c06ee95075a8fe43a` prepared firmware
`mtb1-d6286a78d3` with native profiling enabled and Heltec SPI explicitly at
8 MHz. Both locked targets built; independent artifact and ELF inspection
confirmed Heltec at 8 MHz and XIAO at 2 MHz. Both Heltec units passed an
application-only installation and fresh read-only postinstall checks. Older
paid and UNKNOWN history was retained without a refund.

The [matched v5 transfer](../benchmarks/2026-10-30-sf5-spi8-profile/README.md)
verified 23,900 useful bytes in 23.765471 seconds including process startup,
or 1,005.660692 B/s. The prior 2 MHz sample recorded 2,026.493477 B/s.
DATA retries rose from six to 26; loss patterns were uncontrolled, so this
comparison does not isolate the SPI setting's effect. The sender's mean
native transmit-start call fell from 3.367 to 2.237 ms, while the new file
transfer spent 14.017466 seconds without DATA outstanding. Individual setup
improvements do not establish higher file goodput. A separate
[v6 host-refill sample](../benchmarks/2026-10-30-sf5-scalar-fill-pair-profile/README.md)
retained this SPI8 firmware and both boots, and measured 1,222.394221 B/s.
Its different host policy and losses do not isolate an SPI effect.

Further comparisons need exact prepared manifests, compiled images,
installation proof and fresh finite scopes. Retain the same useful file and
role budgets, include process startup, preserve failed results and compare
native phases on their own clock/boot. The option remains explicit; this
single sample does not qualify 8 MHz signal integrity across operating
conditions or supply RF authority. Alpha.3 release assets remain unchanged.

See the [firmware recipe](FIRMWARE_RECIPE.md) and [native profiling scope](MCU_NATIVE_PROFILING.md).
