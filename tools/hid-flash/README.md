# Bundled hid-flash 2.2.1

This directory vendors the unmodified `hid-flash` command-line uploader from
[Serasidis/STM32_HID_Bootloader](https://github.com/Serasidis/STM32_HID_Bootloader),
release/tag `2.2.1` (`b27e6d1dc1d1fc257a93bf72d213912675c4a5df`).

The executables were extracted from the release asset `hid-flash.zip`. The
source under `src/` comes from the same tag. The upstream project and these
files are distributed under GNU GPL version 2; see
`LICENSE-GPL-2.0.txt`. This is a separate third-party program aggregated with
the MIT-licensed generator and invoked as an external executable.

## Files

- `bin/windows/hid-flash.exe`
- `bin/linux/hid-flash`
- `bin/macos/hid-flash`
- `src/` — complete CLI source and upstream Makefile for this release

## SHA-256

```text
1c8cb0f64168bf44aa70e7c2f437f1dcc0f5aaec0a57f46f2b3513afa8723384  bin/windows/hid-flash.exe
2bfca15c6d3c4135cf4a578081cdd5b0eebe212d0aa1e223f94bc1d0c6b3bb25  bin/linux/hid-flash
bc3c7fc6ae889455d5b26943302b1152a91238b5b35198a3b630e03b65f2d958  bin/macos/hid-flash
```

## Building

Use the upstream `src/Makefile` and platform dependencies documented in the
upstream repository. The generated program accepts:

```text
hid-flash <bin_firmware_file> <comport> <delay (optional)>
```
