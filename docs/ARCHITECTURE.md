# DJI Link Architecture

DJI Link deliberately separates the **desktop brain** from the **USB accessory bridge**.

```text
                           Wi-Fi / TCP
┌──────────────────────┐  ───────────────►  ┌───────────────────────┐
│ PC / DJI Link        │                    │ Raspberry Pi Zero 2 W │
│                      │                    │                       │
│ C++20 desktop app    │                    │ USB OTG / AOA bridge  │
│ SDL2 GUI             │                    │ AP + discovery        │
│ DUML codec           │                    │ bridge service        │
│ telemetry            │                    └───────────┬───────────┘
│ HEVC video           │                                │ USB
│ flight control       │                                ▼
│ camera / gimbal      │                    ┌───────────────────────┐
│ console              │                    │ DJI remote controller │
└──────────────────────┘                    └───────────┬───────────┘
                                                        │ DJI radio
                                                        ▼
                                             ┌───────────────────────┐
                                             │ Mavic Mini 1 / WM160  │
                                             └───────────────────────┘
```

## Responsibilities

### PC

The PC owns the protocol-heavy part of the application:

- DUML framing and checksums;
- composite stream parsing;
- telemetry decoding;
- HEVC video reassembly/decoding;
- flight-control state and input mapping;
- camera/gimbal logic;
- user interface and diagnostics.

### Raspberry Pi

The Pi is intentionally small and boring:

- presents itself to the remote as the USB accessory;
- forwards the byte stream over TCP;
- provides the field Wi-Fi access point;
- exposes discovery/management on port `9911`;
- exposes the flight-data path on port `9910`;
- provides recovery tooling for the field setup.

### Why a Pi is necessary

The stock remote expects the mobile application to connect as a USB **accessory** using Android Open Accessory. A normal desktop OS exposes a USB host, not an accessory device. A Raspberry Pi running in USB peripheral mode can fill that role.

## Data flow

```text
DJI radio link
    ↓
remote controller
    ↓
USB AOA accessory stream
    ↓
Pi bridge
    ↓
TCP / Wi-Fi
    ↓
composite stream parser
    ├── DUML → telemetry / control / camera / gimbal
    └── video → HEVC reassembly → ffmpeg → GUI
```

See the Wiki pages for the detailed protocol and state-machine explanation:

- [Architecture](https://github.com/Kolya080808/DJI-Link/wiki/Architecture)
- [Protocol Overview](https://github.com/Kolya080808/DJI-Link/wiki/Protocol-Overview)
- [Protocol Deep Dive](https://github.com/Kolya080808/DJI-Link/wiki/Protocol-Deep-Dive)
- [Raspberry Pi](https://github.com/Kolya080808/DJI-Link/wiki/Raspberry-Pi)
