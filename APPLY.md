# DJI-Link v0.8.2 — applying this archive

Every path in this archive is already the path it takes in the repository. Unpack it
over the root of your clone and nothing needs to be moved:

```bash
cd <your DJI-Link clone>
git checkout main && git pull
unzip -o /path/to/dji-link-v0.8.2-repo.zip -d .
rm APPLY.md                      # this file — not part of the repo
```

`unzip -o` overwrites without asking. Everything it touches is listed below; nothing
else in the repo is affected.

## What lands where

| Path | Change |
|------|--------|
| `dji_link_beta/pi/ap.sh` | rewritten — channel selection, no more `uap0` teardown on restart |
| `dji_link_beta/pi/netctl.py` | rewritten — explicit `key-mgmt`, non-destructive `connect()`, watchdog, `doctor` |
| `dji_link_beta/pi/setup_pi.sh` | powersave off, udev rule for `uap0`, unit hardening, health report |
| `dji_link_beta/pi/install.sh` | AP health gate + automatic rollback after a bad upgrade |
| `dji_link_beta/pi/update_pi.sh` | skips a tag that already failed and was rolled back |
| `dji_link_beta/pi/README.md` | two-path model, channel behaviour, rescue instructions |
| `dji_link_beta/pi/rescue.sh` | **new** — standalone repair, also runs off the SD card |
| `tests/netctl_parse_test.cpp` | extended with `test_v082_replies()` |
| `tests/netctl_sim_test.py` | **new** — 49 checks against a simulated NetworkManager |
| `tests/ap_channel_test.sh` | **new** — 12 channel-selection cases |
| `tests/fakebin/iw` | **new** — the `iw` stub the above drives |
| `UPDATE.md` | `version: 0.8.2` — **this is the release gate** |
| `README.md` | service list, pinned-tag install URL, pointer to the rescue docs |
| `docs/NETWORK_FIX.md` | v0.8.2 section: root causes, fixes, sources |
| `docs/CI_CD.md` | asset and service lists brought up to date |
| `.github/workflows/release.yml` | one-line fix, see below |

`aoa_device.py`, `bridge.py`, `raw_gadget.py`, `setup_gadget.sh` and
`build_raw_gadget.sh` are byte-identical to what is already in the repo and are not
included.

### The release.yml change

One line in the `pi-installer` job:

```diff
-          bash -n stage/pi/*.sh
+          for f in stage/pi/*.sh; do bash -n "$f"; done
```

`bash -n a.sh b.sh` parses only `a.sh` and turns the rest into positional parameters, so
the glob was syntax-checking `ap.sh` alone — `setup_pi.sh` and the new `rescue.sh` were
never checked. If you would rather not touch CI in this commit, drop
`.github/workflows/release.yml` from the unpack; nothing else depends on it.

## Before tagging

```bash
# gates the CI and the release will run
clang-format --dry-run --Werror $(find . -type d \( -name build -o -name third_party \
    -o -name external -o -name .git -o -name dji_link_beta \) -prune -o \
    -type f \( -name '*.cpp' -o -name '*.hpp' -o -name '*.h' \) -print)
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release && cmake --build build \
  && ctest --test-dir build --output-on-failure

# the Pi side (no Pi, no radio, no root needed)
for f in dji_link_beta/pi/*.sh; do bash -n "$f"; done
python3 -m py_compile dji_link_beta/pi/*.py
python3 tests/netctl_sim_test.py        # expect: all checks passed
bash    tests/ap_channel_test.sh        # expect: all checks passed

git add -A && git commit -m "v0.8.2: Wi-Fi uplink re-join, an AP that stays up, rescue.sh"
git push origin main                    # let CI + Lint go green FIRST
git tag v0.8.2 && git push origin v0.8.2
```

A tag push runs only `release.yml`; `ci.yml` and `lint.yml` do not trigger on tags, so
push the branch first or formatting breakage lands on `main` unnoticed.

If the tag is pushed with a mismatched `UPDATE.md` the `prepare` job fails and the tag is
already on the remote — delete it, fix, re-tag:
`git push --delete origin v0.8.2 && git tag -d v0.8.2`.

## One decision left to you

`UPDATE.md` carries `prerelease: true`, inherited from v0.8.1. GitHub's
`releases/latest/download/...` resolves to the most recent **non**-prerelease, so with
that setting the one-line installer in the README will not fetch v0.8.2 and
`dji-update.timer` will not pick it up either. The docs in this archive give the pinned
URL alongside:

```bash
curl -fsSL https://github.com/Kolya080808/DJI-Link/releases/download/v0.8.2/install-pi.sh | sudo bash
```

Set `prerelease: false` in `UPDATE.md` if you want this release delivered automatically.
