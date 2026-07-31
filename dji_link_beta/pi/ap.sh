#!/usr/bin/env bash
# ap.sh — bring the Pi's Wi-Fi access point up/down with hostapd + dnsmasq.
#
# Why not NetworkManager's `ipv4.method=shared` AP (what netctl.py used before)?
#   1. NM drives the AP through wpa_supplicant, which ADVERTISES WPS in the beacon.
#      Windows then shows "enter the PIN from the router" instead of the passphrase
#      prompt, and the WPS dance often fails the association outright — the
#      "Windows won't join / disconnects and does nothing" symptom. Linux/macOS/iOS
#      ignore the WPS IE and just ask for the password, which is why only Windows
#      breaks. hostapd with `wps_state=0` removes the IE, so every OS sees a plain
#      WPA2 network. See pi/README.md.
#   2. On a Lite image `ipv4.method=shared` can come up without dnsmasq/iptables and
#      then clients associate but get no address / no route ("Pi network has no
#      internet"). Here DHCP, DNS and NAT are set up explicitly and deterministically.
#
# A udev rule creates uap0 during the phy add event, before NetworkManager creates its
# P2P device. The dji-ap systemd unit then starts hostapd after NetworkManager:
#   ExecStartPre = ap.sh pre    (create uap0 + IP + NAT, write hostapd.conf)
#   ExecStart    = ap.sh run    (start dnsmasq, exec hostapd as the main process)
#   ExecStopPost = ap.sh post   (stop dnsmasq, account for the run)
#
# THE AP IS THE LIFELINE. If it is down the Pi cannot be reached at all in the field,
# so everything here is written to fail towards "an AP on a channel that certainly
# works" rather than towards "no AP":
#
#   * The channel is never copied blindly from the uplink. It is intersected with the
#     channels the kernel actually reports as usable for this radio (`iw phy … info`,
#     skipping "disabled" / "no IR" / "radar detection"). Copying the uplink channel
#     unchecked is what killed the AP before: a 5 GHz uplink produced `hw_mode=a` on
#     the 2.4 GHz-only Zero 2 W radio, and an uplink on channel 12/13 produced a
#     channel the world regulatory domain (00) marks NO-IR. hostapd refuses to start
#     in both cases, systemd restarts it, and the Pi has no network at all.
#   * A live uplink channel always wins, even after failed AP starts. The Zero 2 W has
#     one radio, so pinning a different fallback channel guarantees another failure.
#     The safe channel is used only without a usable uplink. The AP unit itself NEVER
#     disconnects wlan0 during boot/restart: doing that can kill the normal LAN/SSH path
#     exactly when the operator is trying to recover the Pi.
#   * `post` does NOT delete uap0. Creating/destroying a brcmfmac virtual interface
#     every few seconds wedges the firmware and takes the station interface down with
#     it. The interface is created once and reused; only hostapd restarts.
#
# The Pi Zero 2 W has ONE radio, so the AP has to share a channel with any uplink the
# station interface (wlan0) has joined; netctl.py restarts this unit after joining an
# uplink, but only when the channel actually changed. With no uplink, no saved Wi-Fi and
# no internet at all, it uses channel 6 and still brings up the local PI_DJI_LINK-* AP.
#
# Not using `set -e`: half of the commands here (iptables -C, killing a stale daemon)
# are expected to fail benignly. Only a failure to create/address uap0 is fatal, and
# that is signalled with an explicit `exit 1` so systemd fails the unit.
set -u

AP_IFACE=uap0
STA_IFACE=wlan0
AP_ADDR=10.42.0.1
AP_CIDR=10.42.0.1/24
AP_SUBNET=10.42.0.0/24
DHCP_LO=10.42.0.50
DHCP_HI=10.42.0.150
AP_PSK=raspberry
NETCTL_PORT=9911
BRIDGE_PORT=9910
RUN_DIR="${DJI_AP_RUN_DIR:-/run/dji-ap}"
STATE_DIR="${DJI_AP_STATE_DIR:-/var/lib/dji-ap}"
HOSTAPD_CONF="$RUN_DIR/hostapd.conf"
DNSMASQ_PID="$RUN_DIR/dnsmasq.pid"
STARTED_AT="$RUN_DIR/started-at"
FAILS="$STATE_DIR/consecutive-failures"

# Channels to fall back to, in order, when the uplink's channel cannot be used. 1/6/11
# are the non-overlapping 2.4 GHz set and are legal in every regulatory domain, so one
# of them is usable even with no country code at all.
SAFE_CHANNELS="6 1 11"
# After this many consecutive short runs, emit the stronger recovery diagnostic. The
# count never overrides a usable live uplink channel.
MAX_FAILS=2
# A run shorter than this counts as a failure for $FAILS.
MIN_GOOD_RUN=30

ap_ssid() {
    # Must match netctl.py's ap_ssid(): PI_DJI_LINK-<last 4 of machine-id>. The PC
    # client recognises a Pi AP by this prefix.
    local mid suf=0000
    mid="$(cat /etc/machine-id 2>/dev/null || true)"
    [ "${#mid}" -ge 4 ] && suf="${mid: -4}"
    printf 'PI_DJI_LINK-%s' "$suf"
}

sta_phy() {
    # The phy behind wlan0 ("phy0"). Needed to ask the kernel which channels this
    # radio may actually transmit on.
    local p
    p="$(iw dev "$STA_IFACE" info 2>/dev/null | sed -n 's/.*wiphy[[:space:]]\+\([0-9]\+\).*/\1/p' | head -n1)"
    [ -n "$p" ] && { printf 'phy%s' "$p"; return 0; }
    p="$(ls -1 /sys/class/ieee80211 2>/dev/null | head -n1)"
    printf '%s' "${p:-phy0}"
}

iface_exists() {
    ip link show "$1" >/dev/null 2>&1
}

iface_is_ap() {
    iw dev "$1" info 2>/dev/null | grep -q '^[[:space:]]*type[[:space:]]\+AP'
}

radio_sanity() {
    rfkill unblock wifi 2>/dev/null || rfkill unblock all 2>/dev/null || true
    modprobe brcmfmac 2>/dev/null || true
    iw dev "$STA_IFACE" set power_save off 2>/dev/null || true
    iw dev "$AP_IFACE" set power_save off 2>/dev/null || true
}

# Every channel this radio may BEACON on right now, one "<freq> <chan>" per line.
#
# `iw phy … info` lists each frequency with its restrictions:
#     * 2412.0 MHz [1] (20.0 dBm)
#     * 2467 MHz [12] (20.0 dBm) (no IR)
#     * 5180 MHz [36] (disabled)
# "no IR" (no initiate radiation) means the channel may be listened on but not
# beaconed on — exactly what an AP does — and "radar detection" means DFS, which
# hostapd will not start on without a CAC. Dropping all three leaves only channels
# hostapd can really come up on, whatever the regulatory domain happens to be.
usable_channels() {
    local phy
    phy="$(sta_phy)"
    iw phy "$phy" info 2>/dev/null | awk '
        /^[[:space:]]*\*[[:space:]]*[0-9]+(\.[0-9]+)?[[:space:]]*MHz[[:space:]]*\[[0-9]+\]/ {
            if ($0 ~ /disabled/ || $0 ~ /no IR/ || $0 ~ /radar detection/) next
            freq = $2; sub(/\..*/, "", freq)
            chan = $0
            sub(/^[^\[]*\[/, "", chan); sub(/\].*/, "", chan)
            print freq, chan
        }'
}

chan_is_usable() {
    local want="$1"
    [ -n "$want" ] || return 1
    usable_channels | awk -v c="$want" '$2 == c { found = 1 } END { exit found ? 0 : 1 }'
}

chan_to_hw() {
    # 2.4 GHz -> g, 5 GHz -> a. Derived from the frequency the kernel reported for the
    # channel rather than from the channel number, which is ambiguous (channel 36
    # exists only on 5 GHz, but channel 1..14 numbering repeats in other bands).
    local want="$1" freq
    freq="$(usable_channels | awk -v c="$want" '$2 == c { print $1; exit }')"
    if [ -n "$freq" ] && [ "$freq" -ge 5000 ] 2>/dev/null; then printf 'a'; else printf 'g'; fi
}

sta_channel() {
    # The channel wlan0's uplink is on, or empty when it has none.
    local freq
    freq="$(iw dev "$STA_IFACE" link 2>/dev/null | sed -n 's/.*freq:[[:space:]]*\([0-9]\+\).*/\1/p' | head -n1)"
    [ -n "$freq" ] || return 0
    if   [ "$freq" -ge 2412 ] && [ "$freq" -le 2472 ]; then printf '%s' $(( (freq - 2407) / 5 ))
    elif [ "$freq" -eq 2484 ]; then printf '14'
    elif [ "$freq" -ge 5000 ]; then printf '%s' $(( (freq - 5000) / 5 ))
    fi
}

wait_for_sta_settle() {
    # NetworkManager.service being active does not mean its boot-time autoconnect has
    # finished. Starting hostapd during that retune produced the observed channel 6 -> 7
    # race. Give NM a short bounded window; an offline field boot returns after three
    # disconnected samples and never waits for internet or network-online.target.
    systemctl is-active --quiet NetworkManager.service 2>/dev/null || return 0
    sleep 2
    local state channel last_channel="" stable=0
    for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
        state="$(nmcli -g GENERAL.STATE dev show "$STA_IFACE" 2>/dev/null | head -n1)"
        channel="$(sta_channel)"
        case "$state" in
            100*|connected*)
                # `iw link` exposes a frequency during the last part of association.
                # Starting hostapd at that first sighting races cfg80211's regulatory
                # update and BCM43430 rejects the otherwise valid shared channel. Wait
                # until NM is fully connected and the channel has stayed put.
                if [ -n "$channel" ] && [ "$channel" = "$last_channel" ]; then
                    stable=$(( stable + 1 ))
                else
                    stable=0
                fi
                last_channel="$channel"
                [ "$stable" -ge 3 ] && return 0
                ;;
            30*|disconnected*)
                # No saved/available uplink is a valid field state. Do not wait for
                # internet: three stable disconnected samples are enough to start the
                # standalone AP on its fallback channel.
                [ -z "$channel" ] && stable=$(( stable + 1 )) || stable=0
                last_channel=""
                [ "$stable" -ge 3 ] && return 0
                ;;
            *)
                stable=0
                last_channel="$channel"
                ;;
        esac
        sleep 1
    done
    echo "[ap] NetworkManager did not settle in 20s; continuing with current radio state" >&2
}

ap_country() {
    # Regulatory domain, best source first:
    #   1. what the kernel is actually enforcing right now (`iw reg get`), including a
    #      domain adopted from the connected uplink's country IE. hostapd requesting a
    #      different cmdline domain while STA is live needlessly retunes regulatory
    #      state on the shared radio during the most timing-sensitive part of startup;
    #   2. the kernel command line — raspi-config's offline fallback on Bookworm
    #      (cfg80211.ieee80211_regdom=XX in /boot/firmware/cmdline.txt);
    #   3. the legacy crda default.
    # Numeric self-managed domains such as 99 and world domain 00 are not ISO country
    # codes and are deliberately ignored by the [A-Z][A-Z] match.
    local c cmdline
    c="$(iw reg get 2>/dev/null | sed -n 's/^country[[:space:]]*\([A-Z][A-Z]\):.*/\1/p' | head -n1)"
    cmdline="$(cat /proc/cmdline 2>/dev/null || true)"
    if [ -z "$c" ]; then
        c="$(printf '%s' "$cmdline" | sed -n 's/.*cfg80211\.ieee80211_regdom=\([A-Z][A-Z]\).*/\1/p' | head -n1)"
    fi
    if [ -z "$c" ] && [ -f /etc/default/crda ]; then
        c="$(sed -n 's/^REGDOMAIN=\([A-Z][A-Z]\)$/\1/p' /etc/default/crda | head -n1)"
    fi
    [ "$c" = "00" ] && c=""
    printf '%s' "$c"
}

fail_count() {
    local n
    n="$(cat "$FAILS" 2>/dev/null || echo 0)"
    case "$n" in ''|*[!0-9]*) n=0 ;; esac
    printf '%s' "$n"
}

record_fail() {
    local why="$1" fails
    mkdir -p "$STATE_DIR"
    fails="$(fail_count)"
    echo $(( fails + 1 )) > "$FAILS"
    echo "[ap] $why (failure $(( fails + 1 ))/$MAX_FAILS)" >&2
}

clear_fail_count() {
    mkdir -p "$STATE_DIR"
    echo 0 > "$FAILS"
}

# Echo "hw_mode channel".
#
# A single brcmfmac radio cannot run STA and AP on different channels. Therefore a live
# uplink always wins, even after earlier hostapd failures: pinning a "safe" channel while
# wlan0 is associated elsewhere guarantees that hostapd will fail with "channel is
# disabled". The safe set is only a fallback while there is no usable uplink channel.
pick_hw_channel() {
    local want fails
    fails="$(fail_count)"
    want="$(sta_channel)"
    if [ -z "$want" ]; then
        local c
        for c in $SAFE_CHANNELS; do
            if chan_is_usable "$c"; then printf 'g %s' "$c"; return 0; fi
        done
        c="$(usable_channels | awk '$1 < 3000 { print $2; exit }')"
        [ -n "$c" ] && { printf 'g %s' "$c"; return 0; }
        c="$(usable_channels | awk 'NR == 1 { print $2 }')"
        if [ -n "$c" ]; then printf '%s %s' "$(chan_to_hw "$c")" "$c"; return 0; fi
        # On a headless field boot the important thing is that hostapd gets a sane local
        # 2.4 GHz config even if iw cannot report the phy yet.
        printf 'g 6'
        return 0
    fi
    if chan_is_usable "$want"; then
        [ "$fails" -ge "$MAX_FAILS" ] && \
            echo "[ap] uplink is live on channel $want; overriding the failure fallback" >&2
        printf '%s %s' "$(chan_to_hw "$want")" "$want"
        return 0
    fi
    echo "[ap] uplink channel $want is not usable for an AP here; using a safe channel" >&2
    for c in $SAFE_CHANNELS; do
        if chan_is_usable "$c"; then printf 'g %s' "$c"; return 0; fi
    done
    # Nothing in the safe set (an odd regulatory domain): take the first 2.4 GHz
    # channel the kernel allows, then the first channel of any band.
    c="$(usable_channels | awk '$1 < 3000 { print $2; exit }')"
    [ -n "$c" ] && { printf 'g %s' "$c"; return 0; }
    c="$(usable_channels | awk 'NR == 1 { print $2 }')"
    if [ -n "$c" ]; then printf '%s %s' "$(chan_to_hw "$c")" "$c"; return 0; fi
    # `iw` missing or the phy told us nothing — channel 6 is the safest blind guess.
    printf 'g 6'
}

ensure_iface() {
    local phy
    radio_sanity

    if iface_exists "$AP_IFACE" && ! iface_is_ap "$AP_IFACE"; then
        echo "[ap] $AP_IFACE exists but is not AP type; recreating it" >&2
        ip link set "$AP_IFACE" down 2>/dev/null || true
        iw dev "$AP_IFACE" del 2>/dev/null || true
    fi

    if ! iface_exists "$AP_IFACE"; then
        # Right after boot the driver may not have registered wlan0/phy yet.
        for _ in 1 2 3 4 5 6 7 8 9 10 11 12; do
            radio_sanity
            if iface_exists "$STA_IFACE"; then
                iw dev "$STA_IFACE" interface add "$AP_IFACE" type __ap 2>/dev/null && break
            fi
            phy="$(sta_phy)"
            [ -n "$phy" ] && iw phy "$phy" interface add "$AP_IFACE" type __ap 2>/dev/null && break
            sleep 1
        done
    fi
    iface_exists "$AP_IFACE" || return 1
    iface_is_ap "$AP_IFACE" || return 1

    # Do NOT reassign the MAC that brcmfmac gave the AP interface. The driver already
    # derives it from wlan0's with the locally-administered bit set, and forcing one
    # is a documented way to end up with clients that associate and get a DHCP lease
    # but cannot exchange traffic with the Pi. Only step in for the degenerate case
    # where the two interfaces really do share one address.
    local base mac first
    base="$(cat "/sys/class/net/$STA_IFACE/address" 2>/dev/null || true)"
    mac="$(cat "/sys/class/net/$AP_IFACE/address" 2>/dev/null || true)"
    if [ -n "$base" ] && [ "$base" = "$mac" ]; then
        first="$(printf '%02x' $(( 0x${base%%:*} | 0x02 )))"
        echo "[ap] $AP_IFACE shares wlan0's MAC; setting $first:${base#*:}"
        ip link set "$AP_IFACE" down 2>/dev/null || true
        ip link set "$AP_IFACE" address "$first:${base#*:}" 2>/dev/null || true
    fi

    ip link set "$AP_IFACE" up || return 1
    # Only touch the address when it is not already exactly right: flushing an address
    # that is in use drops every established connection to 10.42.0.1, which on a
    # restart is the PC client that asked for the restart in the first place.
    if ! ip -4 addr show dev "$AP_IFACE" 2>/dev/null | grep -q "inet $AP_CIDR"; then
        ip addr flush dev "$AP_IFACE" 2>/dev/null || true
        ip addr add "$AP_CIDR" dev "$AP_IFACE" || return 1
    fi
    return 0
}

setup_nat() {
    sysctl -w net.ipv4.ip_forward=1 >/dev/null 2>&1
    # Route AP clients out through whatever uplink exists. "! -o uap0" rather than
    # "-o wlan0" so the rule keeps working if the uplink is ever ethernet or a dongle.
    iptables -t nat -C POSTROUTING -s "$AP_SUBNET" ! -o "$AP_IFACE" -j MASQUERADE 2>/dev/null \
        || iptables -t nat -A POSTROUTING -s "$AP_SUBNET" ! -o "$AP_IFACE" -j MASQUERADE
    iptables -C FORWARD -i "$AP_IFACE" -s "$AP_SUBNET" -j ACCEPT 2>/dev/null \
        || iptables -A FORWARD -i "$AP_IFACE" -s "$AP_SUBNET" -j ACCEPT
    iptables -C FORWARD -o "$AP_IFACE" -d "$AP_SUBNET" -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null \
        || iptables -A FORWARD -o "$AP_IFACE" -d "$AP_SUBNET" -m state --state RELATED,ESTABLISHED -j ACCEPT
    # Reaching the Pi ITSELF from its own AP is a separate path from reaching the
    # internet through it, and it must survive anything the uplink does. On a stock
    # Raspberry Pi OS the INPUT policy is ACCEPT and these are no-ops; they are here so
    # that a Pi which later grows a firewall does not silently lose DHCP/DNS and the
    # netctl/bridge ports on the AP side.
    local port
    for port in 67 53; do
        iptables -C INPUT -i "$AP_IFACE" -p udp --dport "$port" -j ACCEPT 2>/dev/null \
            || iptables -A INPUT -i "$AP_IFACE" -p udp --dport "$port" -j ACCEPT
    done
    for port in 53 "$NETCTL_PORT" "$BRIDGE_PORT" 22; do
        iptables -C INPUT -i "$AP_IFACE" -p tcp --dport "$port" -j ACCEPT 2>/dev/null \
            || iptables -A INPUT -i "$AP_IFACE" -p tcp --dport "$port" -j ACCEPT
    done
    iptables -C INPUT -i "$AP_IFACE" -p icmp -j ACCEPT 2>/dev/null \
        || iptables -A INPUT -i "$AP_IFACE" -p icmp -j ACCEPT
}

teardown_nat() {
    iptables -t nat -D POSTROUTING -s "$AP_SUBNET" ! -o "$AP_IFACE" -j MASQUERADE 2>/dev/null || true
    iptables -D FORWARD -i "$AP_IFACE" -s "$AP_SUBNET" -j ACCEPT 2>/dev/null || true
    iptables -D FORWARD -o "$AP_IFACE" -d "$AP_SUBNET" -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || true
    local port
    for port in 67 53; do
        iptables -D INPUT -i "$AP_IFACE" -p udp --dport "$port" -j ACCEPT 2>/dev/null || true
    done
    for port in 53 "$NETCTL_PORT" "$BRIDGE_PORT" 22; do
        iptables -D INPUT -i "$AP_IFACE" -p tcp --dport "$port" -j ACCEPT 2>/dev/null || true
    done
    iptables -D INPUT -i "$AP_IFACE" -p icmp -j ACCEPT 2>/dev/null || true
}

dnsmasq_alive() {
    local pid
    pid="$(cat "$DNSMASQ_PID" 2>/dev/null || true)"
    [ -n "$pid" ] && [ -d "/proc/$pid" ] || return 1
    tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | grep -q "dnsmasq.*--interface=$AP_IFACE"
}

start_dnsmasq() {
    # A private dnsmasq bound to uap0 only (bind-dynamic), so it never fights the Pi's
    # own resolver or any :53 on wlan0. Public upstreams keep AP clients resolving even
    # if the Pi's /etc/resolv.conf is odd (the user runs Google DNS locally, etc.).
    dnsmasq_alive && return 0
    stop_dnsmasq
    mkdir -p "$RUN_DIR"
    if ! dnsmasq --interface="$AP_IFACE" --bind-dynamic --except-interface=lo \
        --no-resolv --no-hosts --dhcp-authoritative \
        --dhcp-range="$DHCP_LO,$DHCP_HI,255.255.255.0,12h" \
        --dhcp-option=option:router,"$AP_ADDR" \
        --dhcp-option=option:dns-server,"$AP_ADDR" \
        --server=1.1.1.1 --server=8.8.8.8 \
        --pid-file="$DNSMASQ_PID"; then
        echo "[ap] dnsmasq did not start; refusing an AP with no DHCP" >&2
        return 1
    fi
    dnsmasq_alive || {
        echo "[ap] dnsmasq exited immediately; refusing an AP with no DHCP" >&2
        return 1
    }
}

stop_dnsmasq() {
    local pid
    pid="$(cat "$DNSMASQ_PID" 2>/dev/null || true)"
    [ -n "$pid" ] && kill "$pid" 2>/dev/null
    rm -f "$DNSMASQ_PID"
    return 0
}

write_hostapd_conf() {
    local hw ch country
    read -r hw ch <<<"$(pick_hw_channel)"
    country="$(ap_country)"
    mkdir -p "$RUN_DIR"
    {
        echo "interface=$AP_IFACE"
        echo "driver=nl80211"
        echo "ssid=$(ap_ssid)"
        echo "hw_mode=$hw"
        echo "channel=$ch"
        # BCM43430 on the tested 6.18 Raspberry Pi kernel rejects HT during early AP+STA
        # boot with "(extension) channel is disabled", then accepts the identical HT
        # config minutes later. Plain 802.11g starts immediately and its 54 Mbit/s is
        # far above DJI Link's control/telemetry traffic, so make that stable mode
        # explicit instead of leaving hostapd/driver defaults to change underneath us.
        echo "ieee80211n=0"
        echo "wmm_enabled=1"
        echo "auth_algs=1"
        echo "ignore_broadcast_ssid=0"
        # WPA2-PSK, CCMP only. No WPS IE (wps_state=0) -> Windows asks for the password,
        # not a PIN. No TKIP -> no downgrade that some Windows drivers refuse.
        echo "wpa=2"
        echo "wpa_key_mgmt=WPA-PSK"
        echo "rsn_pairwise=CCMP"
        echo "wpa_passphrase=$AP_PSK"
        echo "wps_state=0"
        # BCM43430 (Pi Zero 2 W) firmware resets periodically under AP+STA load,
        # especially with power_save on. During a reset hostapd gets a burst of
        # low-ACK events and kicks the client. disassoc_low_ack=0 prevents hostapd
        # from kicking clients on poor ACK rate so a firmware hiccup does not drop
        # the connection.
        echo "disassoc_low_ack=0"
        # ieee80211d only means anything with a country code, and hostapd refuses to
        # start when it is set without one.
        if [ -n "$country" ]; then
            echo "country_code=$country"
            echo "ieee80211d=1"
        fi
    } > "$HOSTAPD_CONF"
    echo "[ap] hostapd: ssid=$(ap_ssid) hw_mode=$hw channel=$ch country=${country:-none}"
}

cmd_pre() {
    mkdir -p "$RUN_DIR"
    mkdir -p "$STATE_DIR"
    if ! ensure_iface; then
        echo "[ap] could not create/address $AP_IFACE" >&2
        record_fail "could not create/address $AP_IFACE"
        return 1
    fi
    wait_for_sta_settle
    # BCM43430 firmware crashes much more often with power saving enabled while running
    # AP+STA concurrently. Turn it off before hostapd starts. The command is idempotent
    # and benign on other chips. Failures are non-fatal (some kernels ignore the request).
    iw dev "$STA_IFACE" set power_save off 2>/dev/null || true
    iw dev "$AP_IFACE" set power_save off 2>/dev/null || true
    write_hostapd_conf
    setup_nat
    date +%s > "$STARTED_AT"
    echo "[ap] $AP_IFACE up at $AP_ADDR; hostapd conf $HOSTAPD_CONF"
    return 0
}

cmd_run() {
    # Start the companion daemon from ExecStart, not ExecStartPre. A daemon surviving
    # an ExecStartPre command is explicitly diagnosed by systemd as a service lifecycle
    # error. After this shell execs, hostapd is the unit's main PID and dnsmasq remains
    # in the same cgroup, so systemd and cmd_post clean both up together.
    start_dnsmasq || return 1
    exec /usr/sbin/hostapd "$HOSTAPD_CONF"
}

cmd_post() {
    # Count how long hostapd actually stayed up. A run that ends immediately means the
    # config was rejected (bad channel for this regulatory domain, 5 GHz on a 2.4 GHz
    # radio, …). The counter is diagnostic only while wlan0 has a usable live channel:
    # AP+STA must share that channel, so switching away from it can never recover the AP.
    # It still does not disconnect wlan0 here: boot-time recovery must not destroy the
    # Pi's existing LAN/SSH path. A run that lasted is a normal restart and clears it.
    local start now ran fails
    start="$(cat "$STARTED_AT" 2>/dev/null || echo 0)"
    now="$(date +%s)"
    ran=$(( now - start ))
    fails="$(fail_count)"
    if [ "${SERVICE_RESULT:-}" = "success" ]; then
        # systemd sets SERVICE_RESULT for ExecStopPost; "success" means we asked for this
        # stop (a retune, `hotspot off`, an upgrade). Never counted as a failure — the
        # channel we were following is not what ended the run.
        clear_fail_count
    elif [ "$start" -gt 0 ] && [ "$ran" -lt "$MIN_GOOD_RUN" ]; then
        record_fail "hostapd ran only ${ran}s, result=${SERVICE_RESULT:-unknown}"
    elif [ "$start" -le 0 ]; then
        echo "[ap] no successful pre-start timestamp; keeping failure counter at $fails" >&2
    else
        clear_fail_count
    fi
    rm -f "$STARTED_AT"
    # dnsmasq belongs to this hostapd instance; uap0, its address and the NAT rules do
    # not. Deleting and recreating a brcmfmac AP interface on every restart is what
    # wedges the firmware and takes wlan0 down with it, so the interface stays.
    stop_dnsmasq
    return 0
}

# Explicit "hotspot off" teardown. Keep the early-created uap0 object reserved: deleting
# it here means the next "hotspot on" must recreate it after NetworkManager's P2P device,
# undoing the deterministic interface order established at boot. Link-down plus no
# address/hostapd/dnsmasq is fully off from a client's point of view.
cmd_down() {
    stop_dnsmasq
    teardown_nat
    ip addr flush dev "$AP_IFACE" 2>/dev/null || true
    ip link set "$AP_IFACE" down 2>/dev/null || true
    rm -f "$STARTED_AT" "$FAILS"
    return 0
}

# "Is the AP actually serving?" — used by netctl's watchdog, and by a human over SSH.
cmd_health() {
    local rc=0 conf_ch live_ch
    if ! iw dev 2>/dev/null | grep -q "Interface $AP_IFACE"; then
        echo "no $AP_IFACE interface"; return 1
    fi
    ip -4 addr show dev "$AP_IFACE" 2>/dev/null | grep -q "inet $AP_CIDR" \
        || { echo "$AP_IFACE has no $AP_CIDR"; rc=1; }
    pgrep -f "hostapd $HOSTAPD_CONF" >/dev/null 2>&1 || { echo "hostapd not running"; rc=1; }
    dnsmasq_alive || { echo "dnsmasq not running"; rc=1; }
    iptables -t nat -C POSTROUTING -s "$AP_SUBNET" ! -o "$AP_IFACE" -j MASQUERADE 2>/dev/null \
        || { echo "NAT rule missing"; rc=1; }
    conf_ch="$(sed -n 's/^channel=\([0-9]\+\)$/\1/p' "$HOSTAPD_CONF" 2>/dev/null | head -n1)"
    live_ch="$(iw dev "$AP_IFACE" info 2>/dev/null | sed -n 's/.*channel[[:space:]]\+\([0-9]\+\).*/\1/p' | head -n1)"
    if [ -z "$live_ch" ]; then
        echo "$AP_IFACE is not beaconing"; rc=1
    elif [ -n "$conf_ch" ] && [ "$conf_ch" != "$live_ch" ]; then
        # Informational: brcmfmac moves the AP to the station's channel by itself.
        echo "note: beaconing on $live_ch, configured $conf_ch"
    fi
    if [ "$rc" = 0 ]; then
        clear_fail_count
        echo "ok"
    fi
    return "$rc"
}

case "${1:-}" in
    pre)    cmd_pre ;;
    run)    cmd_run ;;
    post)   cmd_post ;;
    down)   cmd_down ;;
    health) cmd_health ;;
    failures) fail_count; echo ;;
    reset-failures) clear_fail_count ;;
    conf)   write_hostapd_conf; cat "$HOSTAPD_CONF" ;;
    # stdout is exactly "<hw_mode> <channel>" and nothing else: netctl.py reads this to
    # decide whether the AP has to be retuned, and it merges stderr into what it reads.
    chan)   pick_hw_channel 2>/dev/null; echo ;;
    *) echo "usage: ap.sh pre|run|post|down|health|failures|reset-failures|conf|chan" >&2; exit 2 ;;
esac
