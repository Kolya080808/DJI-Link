---
title: DJI Link v1.0.0 — Initial release
version: 1.0.0
prerelease: false
---

## Initial release

DJI Link is an unofficial native desktop ground station for the DJI Mavic Mini 1 (WM160). It connects a Windows, Linux, or macOS computer to the remote controller through a small Raspberry Pi USB bridge, without using the phone app as the primary interface.

## Highlights

- H.265/HEVC live video inside the desktop application.
- Live telemetry for attitude, altitude, speed, battery, flight mode, GPS, home state, and diagnostics.
- Flight controls for arm/disarm, takeoff, landing, return-to-home, and continuous virtual-stick movement.
- Mouse-driven yaw and gimbal control with keyboard movement and throttle controls.
- Camera controls for photos, recording, zoom, exposure, and supported camera settings.
- Configurable maximum altitude, distance, and return-to-home altitude.
- Native DUML command console for supported flight, gimbal, camera, and research operations.
- Automatic Raspberry Pi discovery and a guided connection flow.
- Simulator mode for exploring the interface without aircraft hardware.

## Platform packages

- Windows x64, x86, and ARM64 installers and portable archives.
- macOS packages for Apple Silicon and Intel.
- Linux packages for x86_64 and ARM64.
- Raspberry Pi bridge installer and update bundle.

## Technical foundation

The native client is written in C++20 and communicates through DJI's reverse-engineered DUML protocol. The Raspberry Pi presents an Android Open Accessory-compatible USB path to the remote controller and forwards the raw connection to the desktop application over Wi-Fi.

## Important

DJI Link is independent, unofficial software and is not affiliated with or endorsed by DJI. Use only with hardware you own or are authorized to operate. Test new setups with propellers removed whenever possible and follow all applicable aviation and safety rules.
