#!/usr/bin/env bash
# Channel selection in dji_link_beta/pi/ap.sh, without a radio.
#
# The AP dying is the worst failure this project has: when it is down the Pi cannot be
# reached at all. v0.8.1 killed it by copying the uplink's channel into hostapd.conf
# unchecked — a 5 GHz channel onto a 2.4 GHz-only radio, or channel 12/13 under the
# world regulatory domain, which hostapd refuses to start on. These cases pin the
# behaviour that replaced it.
#
# `ap.sh` calls `iw` by bare name, so the stub in tests/fakebin is put on PATH under
# that name — a file called "fake_iw" would never be consulted. The stub renders a
# synthetic `iw phy … info` from the IW_* variables below; nothing here touches real
# hardware, /run, or root.
#
#   bash tests/ap_channel_test.sh          # from the repository root
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AP_SH="$HERE/../dji_link_beta/pi/ap.sh"
[ -f "$AP_SH" ] || { echo "!! not found: $AP_SH (run this from a repo checkout)" >&2; exit 2; }

BIN="$(mktemp -d)"
trap 'rm -rf "$BIN"' EXIT
install -m 0755 "$HERE/fakebin/iw" "$BIN/iw"
PATH="$BIN:$PATH"
export PATH

fails=0
run_case() {
    local desc="$1" want="$2"; shift 2
    local got rundir
    rundir="$(mktemp -d)"
    got="$(env DJI_AP_RUN_DIR="$rundir" "$@" bash "$AP_SH" chan 2>/dev/null)"
    rm -rf "$rundir"
    if [ "$got" = "$want" ]; then
        printf '  ok    %-46s -> %s\n' "$desc" "$got"
    else
        printf '  FAIL  %-46s -> got %-8s want %s\n' "$desc" "'$got'" "'$want'"
        fails=$(( fails + 1 ))
    fi
}

run_failed_case() {
    local desc="$1" want="$2"; shift 2
    local got rundir
    rundir="$(mktemp -d)"
    printf '11\n' > "$rundir/consecutive-failures"
    got="$(env DJI_AP_RUN_DIR="$rundir" "$@" bash "$AP_SH" chan 2>/dev/null)"
    rm -rf "$rundir"
    if [ "$got" = "$want" ]; then
        printf '  ok    %-46s -> %s\n' "$desc" "$got"
    else
        printf '  FAIL  %-46s -> got %-8s want %s\n' "$desc" "'$got'" "'$want'"
        fails=$(( fails + 1 ))
    fi
}

echo "ap.sh channel selection (hw_mode channel):"
# Follow the uplink whenever the kernel says this radio may beacon there.
run_case "no uplink"                          "g 6"  IW_LINK_FREQ=
run_case "uplink on channel 1"                "g 1"  IW_LINK_FREQ=2412
run_case "uplink on channel 6"                "g 6"  IW_LINK_FREQ=2437
run_case "uplink on channel 11"               "g 11" IW_LINK_FREQ=2462
# Do not follow it onto a channel hostapd cannot start on.
run_case "channel 13, world regdomain (no IR)" "g 6"  IW_LINK_FREQ=2472
run_case "channel 13, country set"            "g 13" IW_LINK_FREQ=2472 IW_NOIR= IW_REG=RU
run_case "channel 14 (Japan-only)"            "g 6"  IW_LINK_FREQ=2484
run_case "5 GHz uplink, 2.4 GHz-only radio"   "g 6"  IW_LINK_FREQ=5180
run_case "5 GHz uplink, radio has 5 GHz"      "a 36" IW_LINK_FREQ=5180 IW_BAND5="36 40 44 48"
run_case "5 GHz uplink on a DFS channel"      "g 6"  IW_LINK_FREQ=5260 IW_BAND5="36 52" IW_DFS=52
# Odd regulatory domains: fall back to something, never to nothing.
run_case "channel 6 not allowed, 1 is"        "g 1"  IW_LINK_FREQ= IW_BAND24="1 2 3 4 5"
run_case "only channel 4 allowed"             "g 4"  IW_LINK_FREQ= IW_BAND24="4"
# If earlier bad starts pinned the AP, a live same-radio uplink must still win. Otherwise
# hostapd may try channel 6 while brcmfmac already has the phy on channel 7 and fail with
# "kernel reports: (extension) channel is disabled".
run_failed_case "failed starts, uplink on channel 7" "g 7" IW_LINK_FREQ=2442

if [ "$fails" -ne 0 ]; then
    echo "$fails check(s) failed"
    exit 1
fi
echo "ap.sh channels: all checks passed"
