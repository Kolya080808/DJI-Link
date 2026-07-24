// DJI (WM160) diagnostics tables, ported from diag_codes.py + diag_codes_full.py.
// Decodes the "motors won't start" cause from OSD motor-fail value.
#pragma once

#include <optional>
#include <string>

namespace djilink {

// DiagnosticCode(int) -> human text, if bundled (from diag_codes_full.py).
// std::nullopt when the code has no English text in this build.
std::optional<std::string> diagcode_text(int code);

// Human-readable description of the motor start-failure cause (OSD value).
// Resolves the whole chain: name table -> DiagnosticCode -> code text.
std::string motor_fail_text(int value);

} // namespace djilink
