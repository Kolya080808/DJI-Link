# Contributing To DJI Link

DJI Link is an open-source desktop ground station and reverse-engineering project for the DJI Mavic Mini 1 (WM160). Contributions are welcome in code, documentation, testing, protocol research, and hardware reports.

## Before opening an issue

- Search existing issues first.
- Include the OS, architecture, app version or commit, drone/controller/Pi setup, and exact reproduction steps.
- Remove credentials, private network details, serial numbers, and unredacted flight logs.
- For flight-related problems, describe whether the aircraft was grounded and whether the propellers were removed.

## Development

The native client uses C++20 and CMake. The regular build keeps the GUI enabled:

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Debug
cmake --build build --parallel
ctest --test-dir build --output-on-failure
```

The simulator is useful for UI work without aircraft hardware:

```bash
./build/dji-link --sim --windowed
```

Please keep changes focused. Update the relevant documentation and tests with behavior changes. Do not submit changes that bypass safety checks or geofencing.

## Pull requests

A good pull request explains the user-visible change, lists the platforms tested, and includes screenshots or logs when they clarify the result. For larger protocol or flight-control changes, open an issue first so the approach can be discussed before implementation.

## Safety

Only test with hardware you own or are authorized to operate. Ground-test with propellers removed whenever possible. DJI Link is unofficial software and can cause crashes, flyaways, damage, or injury.
