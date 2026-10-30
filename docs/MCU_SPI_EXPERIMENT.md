# Optional Heltec SPI clock experiment

The firmware recipe supports an explicit, source-only SPI clock experiment for Heltec V4. It has not been prepared, target-built or physically qualified. No throughput gain is established.

`prepare --heltec-spi-hz 8000000` selects 8 MHz for Heltec's radio SPI transactions. Explicit `2000000` provides a 2 MHz control through the same mechanism. Omission preserves the locked upstream 2 MHz behavior, the exact existing generated header, its application identity and the existing `build_options` shape. Native profiling remains independently selected with `--native-profile`; the SPI option does not silently enable it.

For a later separately reviewed preparation, the option accompanies the existing arguments:

```sh
build/firmware-cli/bin/python firmware/meshticulum-bench/prepare_firmware.py prepare \
  --archive-dir build/firmware-archives \
  --output-dir build/firmware-spi-experiment \
  --native-profile --heltec-spi-hz 8000000
```

The recipe accepts only exact integers 2,000,000 and 8,000,000. It appends the selection to the existing force-included `native_profile_config.hpp`, so the application inventory remains eight inputs. Explicit 2 MHz and 8 MHz each have an identity distinct from omission. `SOURCE.json` records `heltec_spi_hz` only when explicitly selected; the build verifier reconstructs the exact header and requires its forced inclusion for both target environments before invoking PlatformIO.

The generated clause overrides RadioLib's public `RADIOLIB_DEFAULT_SPI_SETTINGS` only under the pinned `HELTEC_LORA_V4` macro and board ID 1. It supplies `SPISettings(selected_hz, MSBFIRST, SPI_MODE0)` to the existing Module constructor default. XIAO's `SEEED_XIAO_S3` / board ID 2 retains the upstream 2 MHz setting. Because both target builds share the generated application inventory, XIAO's application identity changes for an explicit option even though its radio clock does not. Unknown or contradictory target macros, mismatched board IDs and external definitions of the SPI setting are compile-time errors for the explicit experiment.

No board source, RadioLib HAL, radio application code, IRQ ordering, RX restart, queue depth, 5 ms / 100 ms listening floor, lease, attempt limit, airtime accounting or serial pacing is changed. The existing constructor API is used; there is no out-of-band `setFrequency`, mutable HAL replacement or ninth application input. Original input archives and the lock remain unchanged.

Source controls verify default byte identity, the exact option/header/identity binding, strict invalid-value refusal before output creation, target selection and macro conflicts with a host C++ constructor spy. The spy exercises the constructor-default convention; it does not compile the radio driver or qualify a board. Drifted metadata, header, identity or missing forced include is refused before PlatformIO. The existing native controls remain separate from any future physical result.

A 255-byte packet's 257-byte WriteBuffer SPI exchange has calculated wire times of 1,028 µs at 2 MHz and 257 µs at 8 MHz. That 771 µs difference is only ideal serial-clock arithmetic. The byte-at-a-time HAL, command setup, BUSY polling, USB work, radio airtime, loss recovery and other overhead remain. Do not infer a driver-call or useful-file speedup from this calculation.

A future experiment needs its own exact prepared manifest, compiled image, installation proof and finite test scope. Compare the same file and original role budgets, include process startup in useful-file goodput, retain all driver/receive failures and compare native phase counts on their own clock/boot. Preserve the default and failed results. Neither this option nor a later successful build supplies RF authority or qualifies 8 MHz signal integrity.

See the [firmware recipe](FIRMWARE_RECIPE.md) and [native profiling scope](MCU_NATIVE_PROFILING.md).
