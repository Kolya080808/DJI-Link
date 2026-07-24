#include "core/diag_codes.hpp"

#include <map>

namespace djilink {
namespace {

// FCMotorStartFailureError value -> DiagnosticCode (diag_codes.py::MOTOR_NOT_START).
const std::map<int, int> kMotorNotStart = {
    {1, 30220},   {2, 30069},   {3, 30070},   {5, 30055},   {6, 30071},   {7, 30072},
    {8, 30073},   {9, 30066},   {10, 30065},  {11, 30074},  {13, 30076},  {14, 30077},
    {16, 30078},  {17, 30079},  {18, 30080},  {19, 30081},  {21, 30082},  {22, 30222},
    {23, 30083},  {24, 30084},  {25, 30085},  {26, 30067},  {27, 30223},  {28, 30086},
    {29, 30253},  {30, 30087},  {31, 30088},  {32, 30089},  {33, 30090},  {34, 30091},
    {35, 30092},  {36, 30093},  {37, 30094},  {38, 30095},  {39, 30096},  {40, 30097},
    {45, 30098},  {47, 30099},  {51, 30700},  {52, 30701},  {61, 30100},  {63, 30101},
    {64, 30102},  {65, 30103},  {66, 30104},  {74, 30105},  {75, 30106},  {76, 30107},
    {77, 30108},  {79, 30212},  {80, 30075},  {83, 30224},  {93, 30109},  {94, 30110},
    {95, 30111},  {96, 30112},  {97, 30113},  {98, 30114},  {99, 30115},  {100, 30116},
    {101, 30117}, {102, 30118}, {103, 30119}, {104, 30209}, {105, 30210}, {106, 30211},
    {107, 30218}, {112, 30120}, {113, 30121}, {114, 30217}, {115, 30122}, {116, 30123},
    {117, 30124}, {118, 30125}, {119, 30126}, {120, 30127}, {122, 30128}, {123, 30129},
    {124, 30068}, {125, 30130}, {127, 30132}, {128, 30225}, {129, 30133}, {130, 30134},
    {131, 30068}, {132, 30135}, {133, 30136}, {134, 30137}, {136, 30138}, {138, 30219},
    {139, 30139}, {146, 30140}, {147, 30239}, {162, 30251}, {163, 30254}, {166, 30260}};

// Human-readable labels for low values (diag_codes.py::MOTOR_FAIL_NAME).
const std::map<int, std::string> kMotorFailName = {
    {0, "NONE — all ok"},
    {1, "COMPASS_ERROR — compass error"},
    {2, "ASSISTANT_PROTECTED — Assistant connected/protected"},
    {3, "DEVICE_LOCKED — device locked"},
    {4, "DISTANCE_LIMIT — distance limit exceeded"},
    {5, "IMU_NEED_CALIBRATION — IMU calibration needed"},
    {6, "IMU_SN_ERROR — IMU serial number error"},
    {7, "IMU_PREHEATING — IMU warming up (wait)"},
    {8, "COMPASS_CALIBRATING — compass calibration in progress"},
    {9, "IMU_NO_ATTITUDE — IMU without attitude"},
    {10, "NO_GPS_AND_NOVICE — no GPS in novice mode"},
    {11, "BATTERY_CELL_ERROR — battery cell error"},
    {12, "BATTERY_COMMUNICATION_ERROR — no communication with battery"},
    {13, "SERIOUS_LOW_VOLTAGE — critically low voltage"},
    {14, "SERIOUS_LOW_POWER — critically low charge"},
    {15, "LOW_VOLTAGE — low voltage"},
    {147, "BACKUP_COMMUNICATE_FAIL — backup channel communication failure"}};

} // namespace

std::string motor_fail_text(int value) {
    auto name_it = kMotorFailName.find(value);
    const bool has_name = name_it != kMotorFailName.end();
    const std::string name = has_name ? name_it->second : std::string();

    auto code_it = kMotorNotStart.find(value);
    const bool has_code = code_it != kMotorNotStart.end();
    const int code = has_code ? code_it->second : 0;

    std::optional<std::string> text = has_code ? diagcode_text(code) : std::nullopt;

    // The name describes this very value, so it wins; the code is only ever an aside.
    if (has_name && text) {
        return name + "  (DiagnosticCode " + std::to_string(code) + ": " + *text + ")";
    }
    if (has_name) {
        return has_code ? (name + " [code " + std::to_string(code) + "]") : name;
    }
    if (text) {
        return *text + " [code " + std::to_string(code) + "]";
    }
    if (has_code) {
        return "DiagnosticCode " + std::to_string(code) + " (raw value " + std::to_string(value) +
               ")";
    }
    return "unknown cause, raw value " + std::to_string(value);
}

} // namespace djilink
