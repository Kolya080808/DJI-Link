---
title: DJI Link v0.8.7 - Pi networking rollback to v0.8.7
version: 0.8.7
prerelease: true
---

## Changed

- **The Raspberry Pi networking implementation is rolled back exactly to v0.8.7.**
  `ap.sh`, `netctl.py`, `setup_pi.sh`, `install.sh`, and the Pi networking README now
  match the v0.8.7 tag byte-for-byte.

- **The experimental AP restart fixes from v0.8.8-v0.9.0 are removed.** This release
  prefers the older, known behavior while reboot recovery is investigated separately.

- **Pi networking tests are rolled back with the implementation.** They no longer assert
  behavior introduced by the removed restart redesign.

## Expected behavior

- Runtime behavior on the Pi is the same as v0.8.7.
- The access point remains usable in the scenarios where v0.8.7 was already working.
- Reliable AP recovery after every reboot is not claimed by this release.

## Not changed

- No C++ client changes are included.
- No new media protocol changes are included.

<!--
Release checklist:
  1. Keep "version" above equal to the tag you push (tag v0.9.1 => version: 0.9.1).
  2. Commit UPDATE.md together with the rollback.
  3. git tag v0.9.1 && git push origin main v0.9.1
Everything below the second "---" (except this comment) becomes the GitHub Release body.
-->
