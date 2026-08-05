---
title: DJI Link v0.9.5 — HUD fixes
version: 0.9.5
prerelease: true
---

## Fixed

- **HUD: the «SATS · GPS» label no longer draws as «SATS Â· GPS».** Both text
  renderers (the stb_truetype path and the bitmap fallback) iterate the string
  byte-by-byte and neither decodes UTF-8, so the two-byte `·` (U+00B7) rendered
  as garbage — in the beta the label only ever passed through the fully Unicode
  Pillow/Tk canvas, where that never mattered. The row now uses ASCII («SATS /
  GPS», values as «12 / 4»).
- **Limits changed in the Escape menu now refresh in the HUD.** The HUD limit
  line («alt<=… dist<=… RTH …») only reflects values read back from the FC via
  0x03/0xF8 — issued once at connect — while 0xF9 writes are not reported back,
  so a slider change applied to the drone but the HUD kept the stale numbers
  until the next connect. After writing «max alt» / «max dist» / «RTH alt» the
  client re-reads the same param, and the readback updates the HUD the way that
  initial read did.