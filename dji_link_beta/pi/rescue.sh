#!/usr/bin/env bash
# rescue.sh — get a Pi that has lost ALL networking back on the air.
#
# Written to be self-contained: it does NOT use netctl.py, ap.sh or anything else from
# the bundle, because the situation it exists for is "the installed bundle is what broke
# the networking". Copy this one file onto the Pi and run it.
#
# What it fixes:
#   * a dji-ap.service that cannot start and is restart-looping, which on a single-radio
#     brcmfmac Pi drags the station interface down with it (no AP *and* no LAN);
#   * an access point configured on a channel this radio may not beacon on (a 5 GHz
#     channel copied from the uplink onto a 2.4 GHz-only Zero 2 W, or channel 12/13 under
#     the world regulatory domain);
#   * a Pi left with no saved Wi-Fi profile at all after a failed join deleted it;
#   * a soft-blocked radio or a wlan0 NetworkManager stopped managing.
#
# Three ways to run it:
#
#   1. On the Pi (HDMI + keyboard, serial console, or SSH over ethernet):
#        sudo bash rescue.sh                       # AP only
#        sudo bash rescue.sh "MySSID" "password"   # AP + rejoin that network
#
#   2. From the SD card, with no console at all. Put this file and a one-line config on
#      the FAT boot partition (the one Windows shows), then add the systemd.run
#      parameters to cmdline.txt — see BOOT-PARTITION RESCUE at the bottom of this file.
#
#   3. Same as (2) but without editing cmdline.txt: drop the file in place, boot the Pi
#      once with a keyboard and run it from the console.
#
# It leaves behind dji-rescue-ap.service — a minimal always-on access point that does not
# depend on the bundle — so the Pi is reachable at 10.42.0.1 after a reboot too. Running
# setup_pi.sh / the installer again removes it and hands the AP back to dji-ap.service.
set -u

AP_IFACE=uap0
STA_IFACE=wlan0
AP_ADDR=10.42.0.1
AP_PSK=raspberry
RESCUE_DIR=/usr/local/lib/dji-rescue
RESCUE_UNIT=/etc/systemd/system/dji-rescue-ap.service
BOOT=/boot/firmware
[ -d "$BOOT" ] || BOOT=/boot

say() { echo "[rescue] $*"; }

if [ "$(id -u)" -ne 0 ]; then
    exec sudo -E bash "${BASH_SOURCE[0]}" "$@"
fi

SSID="${1:-}"
PSK="${2:-}"
COUNTRY=""

# ---------------------------------------------------------------- 0. boot-partition config
# When run from the SD card the user has no shell, so the network to rejoin is read from
# a plain text file they can edit in Notepad.
if [ -f "$BOOT/dji-rescue.conf" ]; then
    say "reading $BOOT/dji-rescue.conf"
    # Only the three keys we expect, so an edited file cannot execute anything.
    while IFS='=' read -r k v; do
        k="${k%%[[:space:]]*}"; v="${v%$'\r'}"
        case "$k" in
            SSID)    [ -z "$SSID" ] && SSID="$v" ;;
            PSK)     [ -z "$PSK" ]  && PSK="$v" ;;
            COUNTRY) COUNTRY="$v" ;;
        esac
    done < "$BOOT/dji-rescue.conf"
fi

# ---------------------------------------------------------------- 1. stop the damage
say "stopping the DJI services so nothing fights this script"
systemctl stop dji-ap.service dji-netctl.service dji-bridge.service 2>/dev/null || true
# The restart loop is the thing that keeps the radio busy; make sure it cannot come back
# mid-rescue. setup_pi.sh re-enables it.
systemctl disable dji-ap.service 2>/dev/null || true
pkill -f 'hostapd /run/dji-ap' 2>/dev/null || true
pkill -f "dnsmasq --interface=$AP_IFACE" 2>/dev/null || true
sleep 1

# ---------------------------------------------------------------- 2. radio sanity
say "unblocking the radio"
rfkill unblock all 2>/dev/null || true
[ -n "$COUNTRY" ] && iw reg set "$COUNTRY" 2>/dev/null || true

# A wedged brcmfmac firmware only comes back with a module reload. Only done when wlan0
# is missing entirely, because a reload drops every wireless connection.
if ! ip link show "$STA_IFACE" >/dev/null 2>&1; then
    say "$STA_IFACE is missing — reloading the Wi-Fi driver"
    iw dev "$AP_IFACE" del 2>/dev/null || true
    modprobe -r brcmfmac_wcc 2>/dev/null || true
    modprobe -r brcmfmac 2>/dev/null || true
    sleep 2
    modprobe brcmfmac 2>/dev/null || true
    sleep 4
fi

say "making sure NetworkManager runs and manages $STA_IFACE"
systemctl enable NetworkManager.service 2>/dev/null || true
systemctl start NetworkManager.service 2>/dev/null || true
sleep 2
nmcli radio wifi on 2>/dev/null || true
nmcli dev set "$STA_IFACE" managed yes 2>/dev/null || true
iw dev "$STA_IFACE" set power_save off 2>/dev/null || true

# Power save off for every future connection too — it is a standing cause of a Pi that
# answers for a few minutes after boot and then goes quiet.
install -d /etc/NetworkManager/conf.d
cat > /etc/NetworkManager/conf.d/98-dji-wifi.conf <<'EOF'
[connection]
wifi.powersave = 2

[device]
wifi.scan-rand-mac-address = no
EOF
cat > /etc/NetworkManager/conf.d/99-dji-uap0-unmanaged.conf <<'EOF'
[keyfile]
unmanaged-devices=interface-name:uap0
EOF
systemctl reload NetworkManager.service 2>/dev/null || nmcli general reload 2>/dev/null || true

# ---------------------------------------------------------------- 3. uplink profile
# Written as a keyfile rather than through nmcli so it also works when this script runs
# before NetworkManager is up (the boot-partition path), and so key-mgmt is present in
# the file itself — a wifi-security section without key-mgmt is exactly what newer
# NetworkManager rejects with "802-11-wireless-security.key-mgmt: property is missing".
if [ -n "$SSID" ]; then
    slug="$(printf '%s' "$SSID" | tr -c 'A-Za-z0-9._-' '_')"
    f="/etc/NetworkManager/system-connections/dji-uplink-${slug}.nmconnection"
    uuid="$(cat /proc/sys/kernel/random/uuid)"
    say "writing a Wi-Fi profile for '$SSID' -> $f"
    install -d -m 0700 /etc/NetworkManager/system-connections
    {
        echo "[connection]"
        echo "id=dji-uplink-${slug}"
        echo "uuid=${uuid}"
        echo "type=wifi"
        echo "interface-name=${STA_IFACE}"
        echo "autoconnect=true"
        echo "autoconnect-priority=5"
        echo
        echo "[wifi]"
        echo "mode=infrastructure"
        echo "ssid=${SSID}"
        echo "powersave=2"
        echo "cloned-mac-address=permanent"
        if [ -n "$PSK" ]; then
            echo
            echo "[wifi-security]"
            echo "key-mgmt=wpa-psk"
            echo "psk=${PSK}"
        fi
        echo
        echo "[ipv4]"
        echo "method=auto"
        echo
        echo "[ipv6]"
        echo "method=auto"
    } > "$f"
    chmod 600 "$f"
    chown root:root "$f"
    nmcli con reload 2>/dev/null || true
    say "joining '$SSID'"
    nmcli --wait 45 con up "dji-uplink-${slug}" 2>&1 | sed 's/^/    /' || true
fi

# ---------------------------------------------------------------- 4. a minimal AP
# Channel 6 with no country code: legal in every regulatory domain, always beaconable,
# and independent of whatever the uplink is doing. Getting the Pi reachable matters more
# than sharing the uplink's channel; the full bundle handles that part properly.
say "installing a minimal always-on access point (dji-rescue-ap.service)"
install -d "$RESCUE_DIR"
cat > "$RESCUE_DIR/ap.sh" <<'RESCUE_AP'
#!/usr/bin/env bash
# Minimal AP for a Pi in recovery: uap0, hostapd on channel 6, dnsmasq, NAT.
set -u
AP_IFACE=uap0
STA_IFACE=wlan0
AP_CIDR=10.42.0.1/24
AP_SUBNET=10.42.0.0/24
RUN_DIR=/run/dji-rescue-ap
CONF="$RUN_DIR/hostapd.conf"
PIDF="$RUN_DIR/dnsmasq.pid"

ssid() {
    local mid suf=0000
    mid="$(cat /etc/machine-id 2>/dev/null || true)"
    [ "${#mid}" -ge 4 ] && suf="${mid: -4}"
    printf 'PI_DJI_LINK-%s' "$suf"
}

case "${1:-}" in
pre)
    mkdir -p "$RUN_DIR"
    iw dev | grep -q "Interface $AP_IFACE" \
        || iw dev "$STA_IFACE" interface add "$AP_IFACE" type __ap 2>/dev/null \
        || iw phy phy0 interface add "$AP_IFACE" type __ap 2>/dev/null || true
    ip link set "$AP_IFACE" up || exit 1
    ip -4 addr show dev "$AP_IFACE" | grep -q "inet $AP_CIDR" \
        || { ip addr flush dev "$AP_IFACE"; ip addr add "$AP_CIDR" dev "$AP_IFACE"; }
    iw dev "$STA_IFACE" set power_save off 2>/dev/null || true
    {
        echo "interface=$AP_IFACE"
        echo "driver=nl80211"
        echo "ssid=$(ssid)"
        echo "hw_mode=g"
        echo "channel=6"
        echo "wmm_enabled=1"
        echo "auth_algs=1"
        echo "wpa=2"
        echo "wpa_key_mgmt=WPA-PSK"
        echo "rsn_pairwise=CCMP"
        echo "wpa_passphrase=raspberry"
        echo "wps_state=0"
        echo "disassoc_low_ack=0"
    } > "$CONF"
    sysctl -w net.ipv4.ip_forward=1 >/dev/null 2>&1
    iptables -t nat -C POSTROUTING -s "$AP_SUBNET" ! -o "$AP_IFACE" -j MASQUERADE 2>/dev/null \
        || iptables -t nat -A POSTROUTING -s "$AP_SUBNET" ! -o "$AP_IFACE" -j MASQUERADE
    iptables -C FORWARD -i "$AP_IFACE" -s "$AP_SUBNET" -j ACCEPT 2>/dev/null \
        || iptables -A FORWARD -i "$AP_IFACE" -s "$AP_SUBNET" -j ACCEPT
    iptables -C FORWARD -o "$AP_IFACE" -d "$AP_SUBNET" -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null \
        || iptables -A FORWARD -o "$AP_IFACE" -d "$AP_SUBNET" -m state --state RELATED,ESTABLISHED -j ACCEPT
    [ -f "$PIDF" ] && kill "$(cat "$PIDF")" 2>/dev/null; rm -f "$PIDF"
    dnsmasq --interface="$AP_IFACE" --bind-dynamic --except-interface=lo \
        --no-resolv --no-hosts --dhcp-authoritative \
        --dhcp-range=10.42.0.50,10.42.0.150,255.255.255.0,12h \
        --dhcp-option=option:router,10.42.0.1 \
        --dhcp-option=option:dns-server,10.42.0.1 \
        --server=1.1.1.1 --server=8.8.8.8 --pid-file="$PIDF" || true
    ;;
post)
    [ -f "$PIDF" ] && kill "$(cat "$PIDF")" 2>/dev/null; rm -f "$PIDF"
    ;;
*) echo "usage: ap.sh pre|post" >&2; exit 2 ;;
esac
RESCUE_AP
chmod +x "$RESCUE_DIR/ap.sh"

cat > "$RESCUE_UNIT" <<EOF
[Unit]
Description=DJI Link rescue access point (minimal, channel 6)
After=NetworkManager.service
Wants=NetworkManager.service

[Service]
Type=simple
ExecStartPre=/bin/bash $RESCUE_DIR/ap.sh pre
ExecStart=/usr/sbin/hostapd /run/dji-rescue-ap/hostapd.conf
ExecStopPost=/bin/bash $RESCUE_DIR/ap.sh post
Restart=always
RestartSec=5
StartLimitIntervalSec=0
User=root

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable dji-rescue-ap.service >/dev/null 2>&1 || true
systemctl restart dji-rescue-ap.service || true
sleep 4

# ---------------------------------------------------------------- 5. clean up the boot hook
# When launched via systemd.run from cmdline.txt, take the parameters back out so the
# next boot is a normal one. Done before the report so a reboot cannot leave it behind.
if [ -f "$BOOT/cmdline.txt" ] && grep -q 'systemd.run=.*dji-rescue' "$BOOT/cmdline.txt"; then
    say "removing the one-shot rescue parameters from $BOOT/cmdline.txt"
    sed -i -e 's# systemd\.run=[^ ]*dji-rescue[^ ]*##g' \
           -e 's# systemd\.run_success_action=[^ ]*##g' \
           -e 's# systemd\.run_failure_action=[^ ]*##g' \
           -e 's# systemd\.unit=kernel-command-line\.target##g' "$BOOT/cmdline.txt"
    sync
fi

# ---------------------------------------------------------------- 6. report
echo
say "--------------------------------------------------------------"
say "access point : $(systemctl is-active dji-rescue-ap.service)  (SSID PI_DJI_LINK-*, password '$AP_PSK', gateway $AP_ADDR)"
say "interfaces   :"
ip -brief -4 addr 2>/dev/null | sed 's/^/    /'
say "uplink       : $(nmcli -t -f GENERAL.CONNECTION dev show "$STA_IFACE" 2>/dev/null | cut -d: -f2-)"
say "wifi profiles:"
nmcli -t -f NAME,TYPE con show 2>/dev/null | grep -i wireless | sed 's/^/    /' || echo "    none"
say "--------------------------------------------------------------"
say "Next: join the PI_DJI_LINK-* network (or reach the Pi on your LAN) and reinstall:"
say "  curl -fsSL https://github.com/Kolya080808/DJI-Link/releases/latest/download/install-pi.sh | sudo bash"
say "That removes this rescue AP and restores the full dji-ap service."

# ------------------------------------------------------------------------------------
# BOOT-PARTITION RESCUE (no console, no network at all)
#
# On another computer, put the SD card in and open the small FAT partition Windows shows
# (it holds config.txt / cmdline.txt). Then:
#
#   1. Copy this file there as  dji-rescue.sh
#   2. Optionally create  dji-rescue.conf  next to it, so the Pi rejoins your Wi-Fi:
#          SSID=MyHomeNetwork
#          PSK=MyPassword
#          COUNTRY=RU
#   3. Open cmdline.txt (ONE long line — do not add line breaks) and append, on that
#      same line:
#          systemd.run=/boot/firmware/dji-rescue.sh systemd.run_success_action=reboot systemd.unit=kernel-command-line.target
#      On an older image whose boot partition mounts at /boot, use /boot/dji-rescue.sh.
#   4. Put the card back, power the Pi up and wait ~2 minutes. It runs the script, takes
#      those parameters back out of cmdline.txt and reboots into a Pi with a working
#      access point (and your Wi-Fi, if you filled in dji-rescue.conf).
#
# This is the same mechanism Raspberry Pi Imager uses for its own firstrun.sh.
# ------------------------------------------------------------------------------------
