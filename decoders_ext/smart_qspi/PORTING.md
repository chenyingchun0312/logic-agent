# Smart QSPI adapter

Source: DreamSourceLab DSView v1.3.2 `decoders/qspi/pd.py`.

Changes:
- directory renamed to match decoder id `smart_qspi`;
- annotation ids made unique for upstream libsigrokdecode;
- DSView-only `self.matched` checks replaced with edge-change tracking;
- decode loop waits for any clock/CS edge and qualifies rising/falling
  explicitly, because upstream libsigrokdecode keeps returning samples while
  the clock level is unchanged;
- `cs_polarity` is honoured in the CS-asserted check.

Validation:
- `sigrok-cli --show -P smart_qspi` loads the decoder;
- synthetic SPI Mode 0 transaction `03 00 12 34 A5 5A` decodes to
  `Command : Read Data Bytes`, `24-Bit Address : 0x1234`, `Read Data : 0xa5`,
  `Read Data : 0x5a`;
- transaction bytes must remain within one CS-asserted interval.

`scripts/test.ps1` and `scripts/release.ps1` copy this directory over the upstream decoder tree in `.deps/sigrok/decoders`.
License: GPL-2.0-or-later (see upstream headers).
