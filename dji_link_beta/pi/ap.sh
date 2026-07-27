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
# Driven by a systemd unit (dji-ap.service) created by setup_pi.sh:
#   ExecStartPre = ap.sh pre    (create uap0 + IP + NAT + dnsmasq, write hostapd.conf)
#   ExecStart    = hostapd /run/dji-ap/hostapd.conf   (the foreground main process)
#   ExecStopPost = ap.sh post   (tear the extras down again)
#
# The Pi Zero 2 W has ONE radio, so the AP must share a channel with any uplink the
# station interface (wlan0) has joined. `pre` therefore reads wlan0's current channel
# and tunes the AP to it; netctl.py restarts this unit after joining an uplink so the
# AP re-tunes. With no uplink it defaults to channel 6.
#
# Not using `set -e`: half of the commands here (iptables -C, iw dev del, killing a
# stale daemon) are expected to fail benignly. Only a failure to create/address uap0
# is fatal, and that is signalled with an explicit `exit 1` so systemd fails the unit.
set -u

AP_IFACE=uap0
STA_IFACE=wlan0
AP_ADDR=10.42.0.1
AP_CIDR=10.42.0.1/24
AP_SUBNET=10.42.0.0/24
DHCP_LO=10.42.0.50
DHCP_HI=10.42.0.150
AP_PSK=raspberry
RUN_DIR=/run/dji-ap
HOSTAPD_CONF="$RUN_DIR/hostapd.conf"
DNSMASQ_PID="$RUN_DIR/dnsmasq.pid"

ap_ssid() {
    # Must match netctl.py's ap_ssid(): PI_DJI_LINK-<last 4 of machine-id>. The PC
    # client recognises a Pi AP by this prefix.
    local mid suf=0000
    mid="$(cat /etc/machine-id 2>/dev/null || true)"
    [ "${#mid}" -ge 4 ] && suf="${mid: -4}"
    printf 'PI_DJI_LINK-%s' "$suf"
}

# Echo "hw_mode channel". Follows wlan0's channel when it has an uplink (single radio,
# shared channel), else 2.4 GHz channel 6.
ap_hwmode_channel() {
    local freq hw=g ch=6
    freq="$(iw dev "$STA_IFACE" link 2>/dev/null | sed -n 's/.*freq:[[:space:]]*\([0-9]\+\).*/\1/p' | head -n1)"
    if [ -n "$freq" ]; then
        if [ "$freq" -ge 2412 ] && [ "$freq" -le 2472 ]; then
            hw=g; ch=$(( (freq - 2407) / 5 ))
        elif [ "$freq" -eq 2484 ]; then
            hw=g; ch=14
        elif [ "$freq" -ge 5000 ]; then
            hw=a; ch=$(( (freq - 5000) / 5 ))
        fi
    fi
    printf '%s %s' "$hw" "$ch"
}

ap_country() {
    local c
    c="$(iw reg get 2>/dev/null | sed -n 's/^country \([A-Z0-9][A-Z0-9]\):.*/\1/p' | head -n1)"
    [ "$c" = "00" ] && c=""
    printf '%s' "$c"
}

ensure_iface() {
    if ! iw dev 2>/dev/null | grep -q "Interface $AP_IFACE"; then
        iw dev "$STA_IFACE" interface add "$AP_IFACE" type __ap || return 1
    fi
    # Stable, locally-administered MAC derived from wlan0's — keeps the laptop from
    # seeing a "new" network each boot, and never collides with the station's own MAC.
    local base first mac
    base="$(cat "/sys/class/net/$STA_IFACE/address" 2>/dev/null || true)"
    if [ -n "$base" ]; then
        first="$(printf '%02x' $(( 0x${base%%:*} | 0x02 )))"
        mac="$first:${base#*:}"
        ip link set "$AP_IFACE" down 2>/dev/null || true
        ip link set "$AP_IFACE" address "$mac" 2>/dev/null || true
    fi
    ip link set "$AP_IFACE" up || return 1
    ip addr flush dev "$AP_IFACE" 2>/dev/null || true
    ip addr add "$AP_CIDR" dev "$AP_IFACE" || return 1
    return 0
}

setup_nat() {
    sysctl -w net.ipv4.ip_forward=1 >/dev/null 2>&1
    iptables -t nat -C POSTROUTING -s "$AP_SUBNET" ! -o "$AP_IFACE" -j MASQUERADE 2>/dev/null \
        || iptables -t nat -A POSTROUTING -s "$AP_SUBNET" ! -o "$AP_IFACE" -j MASQUERADE
    iptables -C FORWARD -i "$AP_IFACE" -s "$AP_SUBNET" -j ACCEPT 2>/dev/null \
        || iptables -A FORWARD -i "$AP_IFACE" -s "$AP_SUBNET" -j ACCEPT
    iptables -C FORWARD -o "$AP_IFACE" -d "$AP_SUBNET" -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null \
        || iptables -A FORWARD -o "$AP_IFACE" -d "$AP_SUBNET" -m state --state RELATED,ESTABLISHED -j ACCEPT
}

teardown_nat() {
    iptables -t nat -D POSTROUTING -s "$AP_SUBNET" ! -o "$AP_IFACE" -j MASQUERADE 2>/dev/null || true
    iptables -D FORWARD -i "$AP_IFACE" -s "$AP_SUBNET" -j ACCEPT 2>/dev/null || true
    iptables -D FORWARD -o "$AP_IFACE" -d "$AP_SUBNET" -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || true
}

start_dnsmasq() {
    # A private dnsmasq bound to uap0 only (bind-dynamic), so it never fights the Pi's
    # own resolver or any :53 on wlan0. Public upstreams keep AP clients resolving even
    # if the Pi's /etc/resolv.conf is odd (the user runs Google DNS locally, etc.).
    [ -f "$DNSMASQ_PID" ] && kill "$(cat "$DNSMASQ_PID" 2>/dev/null)" 2>/dev/null || true
    rm -f "$DNSMASQ_PID"
    dnsmasq --interface="$AP_IFACE" --bind-dynamic --except-interface=lo \
        --no-resolv --no-hosts --dhcp-authoritative \
        --dhcp-range="$DHCP_LO,$DHCP_HI,255.255.255.0,12h" \
        --dhcp-option=option:router,"$AP_ADDR" \
        --dhcp-option=option:dns-server,"$AP_ADDR" \
        --server=1.1.1.1 --server=8.8.8.8 \
        --pid-file="$DNSMASQ_PID" 2>/dev/null || true
}

stop_dnsmasq() {
    [ -f "$DNSMASQ_PID" ] && kill "$(cat "$DNSMASQ_PID" 2>/dev/null)" 2>/dev/null || true
    rm -f "$DNSMASQ_PID"
}

write_hostapd_conf() {
    local hw ch country
    read -r hw ch <<<"$(ap_hwmode_channel)"
    country="$(ap_country)"
    mkdir -p "$RUN_DIR"
    {
        echo "interface=$AP_IFACE"
        echo "driver=nl80211"
        echo "ssid=$(ap_ssid)"
        echo "hw_mode=$hw"
        echo "channel=$ch"
        echo "ieee80211n=1"
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
        if [ -n "$country" ]; then
            echo "country_code=$country"
            echo "ieee80211d=1"
        fi
    } > "$HOSTAPD_CONF"
}

cmd_pre() {
    if ! ensure_iface; then
        echo "[ap] could not create/address $AP_IFACE" >&2
        return 1
    fi
    write_hostapd_conf
    setup_nat
    start_dnsmasq
    echo "[ap] uap0 up at $AP_ADDR; hostapd conf $HOSTAPD_CONF"
    return 0
}

cmd_post() {
    stop_dnsmasq
    teardown_nat
    ip addr flush dev "$AP_IFACE" 2>/dev/null || true
    iw dev "$AP_IFACE" del 2>/dev/null || true
    return 0
}

case "${1:-}" in
    pre)  cmd_pre ;;
    post) cmd_post ;;
    conf) write_hostapd_conf; cat "$HOSTAPD_CONF" ;;
    *) echo "usage: ap.sh pre|post|conf" >&2; exit 2 ;;
esac
