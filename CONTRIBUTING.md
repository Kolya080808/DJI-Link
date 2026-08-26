# Contributing To DJI Link

DJI Link is an open-source desktop ground station and reverse-engineering project for the DJI Mavic Mini 1 (WM160). Contributions are especially valuable in protocol/media research, hardware testing, documentation, future-feature ideas, and longer-term AI research. Code contributions are welcome too, but for several areas the research is the real bottleneck.

## Before opening an issue

- Search existing issues first.
- Include the OS, architecture, app version or commit, drone/controller/Pi setup, and exact reproduction steps.
- Remove credentials, private network details, serial numbers, and unredacted flight logs.
- For flight-related problems, describe whether the aircraft was grounded and whether the propellers were removed.
- For protocol/reverse-engineering reports, distinguish what was **observed**, what was **inferred**, and what was **hardware-verified**.

## Where help is most needed

Some parts of DJI Link need **research more than implementation**. If you can capture, compare, investigate, document, or reproduce something, that can be more valuable than a code contribution.

### Media research — highest priority

The media side is currently one of the biggest unknowns in the project. Unlike the flight/control path, there is very little reliable public research to build on, and the existing media implementation is not yet based on a sufficiently verified understanding of the protocol.

Useful contributions include:

- packet captures and protocol analysis around media operations;
- observations from DJI Fly or other DJI applications/devices;
- research into media listing, metadata, download/transfer, delete, storage, and camera/media workflows;
- comparisons with other DJI aircraft where the protocol or behavior may provide useful clues;
- independently reproducible experiments and leads, even when they do not yet explain the protocol.

**Research is more valuable than implementation here.** Once the protocol is understood, the implementation can be done inside DJI Link.

### Xbox controller testing

Controller support is planned for the 2.0.0 direction. PS-controller testing is available locally, but I do not currently have an Xbox controller.

If you have an Xbox controller, testing input mappings, axes, buttons, dead zones, and platform-specific behavior would be especially useful.

### Documentation

The Wiki and reverse-engineering corpus are growing quickly, and keeping everything consistent is becoming difficult.

Help with finding outdated or contradictory statements, fixing broken links, restructuring pages, improving cross-links between research and implementation, turning experiments into clear reproducible notes, and reviewing terminology/evidence levels is very welcome.

You do **not** need to write code to make a useful documentation contribution.

### AI research and future capabilities

AI is a longer-term direction for the 3.0.0 roadmap. Ideas, experiments, references, and research are welcome now even if implementation comes later.

Interesting directions include bringing useful capabilities found on newer drones to the Mavic Mini where technically possible, exploring capabilities DJI never shipped for the Mini, camera/media assistance, scene understanding, intelligent diagnostics, and higher-level automation.

Flight-critical behavior should remain deterministic and explicitly user-controlled.

### Ideas for development

Have an idea for a useful, unusual, or technically interesting feature? **Open an issue or start a Discussion.** The project is deliberately not limited to reproducing the features of the original DJI app.

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
