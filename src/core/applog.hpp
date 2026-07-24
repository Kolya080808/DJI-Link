// File logging in the Beat Saber style, ported from applog.py.
// Current run writes logs/latest.log; on the next startup the previous latest.log
// is renamed to a dated archive and archives older than KEEP_DAYS are deleted.
// An in-memory tail feeds the GUI's live log pane.
#pragma once

#include <string>
#include <vector>

namespace djilink::applog {

inline constexpr int KEEP_DAYS = 7;

// Initialise file logging. Call once at startup before anything logs.
void setup(bool verbose = false);

void info(const std::string& line);  // milestones — file + tail
void debug(const std::string& line); // verbose detail — file only (unless verbose)

std::vector<std::string> tail(); // recent log lines (bounded, for the GUI)
std::string latest_path();       // absolute path of logs/latest.log

} // namespace djilink::applog
