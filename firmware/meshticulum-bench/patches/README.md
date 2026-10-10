# Framework modification and notices

The HWCDC patch backports the USB FIFO/TX fixes from Espressif commit
`2ecad233178bfbba9c584ffd9f67e941d84adbb7` into the hash-pinned framework file.
Its original and patched hashes are in `../inputs.lock.json`.

`cores/esp32/HWCDC.cpp` carries Apache-2.0 terms and Copyright 2015–2024
Espressif Systems (Shanghai) PTE LTD. Both the primary framework file and the
selected upstream revision retain that header. These per-file terms apply to
the recorded patch; the project Apache-2.0 license supplies the full text.

The framework package separately declares LGPL-2.1-or-later. The supplementary
`LICENSE.arduino-esp32` is the pinned framework-level upstream license text and
is copied to the prepared framework. It does not replace the source files'
own notices or complete the precompiled SDK, firmware-output or relinking
review. The recipe and build records keep complete notice closure false.
