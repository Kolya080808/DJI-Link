# Contributing to DJI Link

DJI Link is an open-source desktop ground station and reverse-engineering project for the DJI Mavic Mini 1 (WM160). Contributions are welcome in code, documentation, testing, protocol research, simulator work, packaging, and hardware reports.

## Before opening an issue

- Search existing issues first.
- Include the OS, architecture, app version or commit, drone/controller/Pi setup, and exact reproduction steps.
- Remove credentials, private network details, serial numbers, and unredacted flight logs.
- For flight-related problems, say whether the aircraft was grounded and whether the propellers were removed.
- For protocol/reverse-engineering reports, state what was **observed**, what was **inferred**, and whether it was **hardware-verified**.

## Development

The native client uses C++20 and CMake. The normal debug build keeps the GUI enabled:

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Debug
cmake --build build --parallel
ctest --test-dir build --output-on-failure
```

For UI work, use the simulator instead of aircraft hardware:

```bash
./build/dji-link --sim --windowed
```

Please keep changes focused. Update the relevant documentation and tests with behavior changes. Do not submit changes that bypass safety checks or geofencing.

## Reverse-engineering contributions

A useful protocol contribution should ideally include:

- target aircraft/controller and firmware/app version;
- transport path;
- the high-level action that was being tested;
- request/response or push captures when sharing them is legal and safe;
- timing/state assumptions;
- evidence level: static, correlated, captured, hardware-verified, or application-verified;
- the corresponding implementation or research note.

Do not turn a single successful experiment into a permanent protocol fact without documenting the uncertainty.

## Pull requests

A good pull request explains the user-visible change, lists platforms tested, and includes screenshots/logs when they clarify the result. For larger protocol or flight-control changes, open an issue first so the approach can be discussed before implementation.

## Safety

Only test with hardware you own or are authorized to operate. Ground-test with propellers removed whenever possible. Keep the official remote available as a manual fallback. DJI Link is unofficial software and can cause crashes, flyaways, damage, or injury.
