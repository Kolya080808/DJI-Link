#include "gui/gui.hpp"

#include "core/applog.hpp"
#include "core/client.hpp"
#include "core/ffmpeg.hpp"
#include "core/netfind.hpp"
#include "core/transport.hpp"
#include "core/updater.hpp"

#include <algorithm>
#include <array>
#include <atomic>
#include <cctype>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <functional>
#include <future>
#include <map>
#include <memory>
#include <mutex>
#include <optional>
#include <SDL.h>
#include <set>
#include <sstream>
#include <string>
#include <thread>
#include <utility>
#include <vector>

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#else
#include <csignal>
#include <fcntl.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>
#endif

// Vendored single-header TrueType rasteriser (public domain). Only ever fed a trusted
// system font. Header lives in third_party/ so lint/clang-format leave it alone.
#define STB_TRUETYPE_IMPLEMENTATION
#include "stb/stb_truetype.h"

namespace djilink::gui {
namespace {

constexpr int kWinW = 1100;
constexpr int kWinH = 720;
constexpr int kVideoW = 640;
constexpr int kVideoH = 360;
constexpr double kMouseGimbalSens = 0.15;

struct Color {
    std::uint8_t r, g, b, a = 255;
};

constexpr Color BG{18, 20, 26, 255};
constexpr Color PANEL{28, 31, 40, 240};
constexpr Color PANEL_HI{38, 42, 54, 255};
constexpr Color ACCENT{90, 160, 255, 255};
constexpr Color ACCENT_HI{120, 190, 255, 255};
constexpr Color TEXT{222, 228, 236, 255};
constexpr Color MUTED{140, 150, 165, 255};
constexpr Color GOOD{120, 220, 140, 255};
constexpr Color WARN{255, 180, 110, 255};
constexpr Color BAD{255, 120, 120, 255};

SDL_Surface* make_window_icon() {
    constexpr int size = 64;
    SDL_Surface* icon = SDL_CreateRGBSurfaceWithFormat(0, size, size, 32, SDL_PIXELFORMAT_RGBA32);
    if (!icon)
        return nullptr;
    const auto pixel = [&](std::uint8_t r, std::uint8_t g, std::uint8_t b) {
        return SDL_MapRGBA(icon->format, r, g, b, 255);
    };
    const std::uint32_t bg = pixel(7, 16, 23);
    const std::uint32_t mint = pixel(115, 247, 197);
    const std::uint32_t blue = pixel(120, 168, 255);
    SDL_FillRect(icon, nullptr, bg);
    auto put = [&](int x, int y, std::uint32_t color, int radius = 0) {
        for (int yy = -radius; yy <= radius; ++yy) {
            for (int xx = -radius; xx <= radius; ++xx) {
                if (xx * xx + yy * yy > radius * radius)
                    continue;
                const int px = x + xx, py = y + yy;
                if (px >= 0 && px < size && py >= 0 && py < size)
                    static_cast<std::uint32_t*>(icon->pixels)[py * (icon->pitch / 4) + px] = color;
            }
        }
    };
    auto line = [&](int x0, int y0, int x1, int y1, std::uint32_t color, int width = 2) {
        const int dx = std::abs(x1 - x0), sx = x0 < x1 ? 1 : -1;
        const int dy = -std::abs(y1 - y0), sy = y0 < y1 ? 1 : -1;
        int err = dx + dy;
        while (true) {
            put(x0, y0, color, width);
            if (x0 == x1 && y0 == y1)
                break;
            const int twice = 2 * err;
            if (twice >= dy) {
                err += dy;
                x0 += sx;
            }
            if (twice <= dx) {
                err += dx;
                y0 += sy;
            }
        }
    };
    // Exact geometry of the mark in docs/dji-link-logo.svg, scaled into 64x64.
    line(18, 32, 11, 40, mint);
    line(46, 32, 53, 40, mint);
    line(25, 28, 18, 23, mint);
    line(39, 28, 46, 23, mint);
    line(25, 28, 39, 28, mint);
    line(27, 36, 37, 36, mint);
    put(11, 40, mint, 2);
    put(53, 40, blue, 2);
    for (int y = 26; y <= 38; ++y)
        for (int x = 27; x <= 38; ++x)
            if (x >= 27 && x <= 38 && y >= 26 && y <= 38)
                static_cast<std::uint32_t*>(icon->pixels)[y * (icon->pitch / 4) + x] = blue;
    return icon;
}

void set_color(SDL_Renderer* r, Color c) {
    SDL_SetRenderDrawColor(r, c.r, c.g, c.b, c.a);
}

void fill(SDL_Renderer* r, const SDL_Rect& rc, Color c) {
    set_color(r, c);
    SDL_RenderFillRect(r, &rc);
}

void outline(SDL_Renderer* r, const SDL_Rect& rc, Color c) {
    set_color(r, c);
    SDL_RenderDrawRect(r, &rc);
}

constexpr double kPi = 3.14159265358979323846;

// Rounded-rectangle fill/outline — the beta draws every panel, button and input with
// border_radius, so these give the C++ GUI the same soft-cornered look.
void fill_round(SDL_Renderer* r, const SDL_Rect& rc, int rad, Color c) {
    rad = std::max(0, std::min(rad, std::min(rc.w, rc.h) / 2));
    if (rad == 0) {
        fill(r, rc, c);
        return;
    }
    set_color(r, c);
    SDL_Rect mid{rc.x, rc.y + rad, rc.w, rc.h - 2 * rad};
    SDL_RenderFillRect(r, &mid);
    for (int i = 0; i < rad; ++i) {
        const int inset =
            rad - static_cast<int>(
                      std::sqrt(static_cast<double>(rad * rad - (rad - i) * (rad - i))) + 0.5);
        SDL_Rect top{rc.x + inset, rc.y + i, rc.w - 2 * inset, 1};
        SDL_Rect bot{rc.x + inset, rc.y + rc.h - 1 - i, rc.w - 2 * inset, 1};
        SDL_RenderFillRect(r, &top);
        SDL_RenderFillRect(r, &bot);
    }
}

void outline_round(SDL_Renderer* r, const SDL_Rect& rc, int rad, Color c) {
    rad = std::max(0, std::min(rad, std::min(rc.w, rc.h) / 2));
    set_color(r, c);
    SDL_RenderDrawLine(r, rc.x + rad, rc.y, rc.x + rc.w - rad - 1, rc.y);
    SDL_RenderDrawLine(r, rc.x + rad, rc.y + rc.h - 1, rc.x + rc.w - rad - 1, rc.y + rc.h - 1);
    SDL_RenderDrawLine(r, rc.x, rc.y + rad, rc.x, rc.y + rc.h - rad - 1);
    SDL_RenderDrawLine(r, rc.x + rc.w - 1, rc.y + rad, rc.x + rc.w - 1, rc.y + rc.h - rad - 1);
    for (int i = 0; i <= 90; ++i) {
        const double a = i * kPi / 180.0;
        const int dx = static_cast<int>(rad - rad * std::cos(a) + 0.5);
        const int dy = static_cast<int>(rad - rad * std::sin(a) + 0.5);
        SDL_RenderDrawPoint(r, rc.x + dx, rc.y + dy);
        SDL_RenderDrawPoint(r, rc.x + rc.w - 1 - dx, rc.y + dy);
        SDL_RenderDrawPoint(r, rc.x + dx, rc.y + rc.h - 1 - dy);
        SDL_RenderDrawPoint(r, rc.x + rc.w - 1 - dx, rc.y + rc.h - 1 - dy);
    }
}

const char* const* glyph(char ch) {
    static const char* blank[] = {"00000", "00000", "00000", "00000", "00000", "00000", "00000"};
    static const char* unknown[] = {"11111", "10001", "00110", "00100", "00100", "00000", "00100"};
    static const std::map<char, std::array<const char*, 7>> table = {
        {'A', {"01110", "10001", "10001", "11111", "10001", "10001", "10001"}},
        {'B', {"11110", "10001", "10001", "11110", "10001", "10001", "11110"}},
        {'C', {"01111", "10000", "10000", "10000", "10000", "10000", "01111"}},
        {'D', {"11110", "10001", "10001", "10001", "10001", "10001", "11110"}},
        {'E', {"11111", "10000", "10000", "11110", "10000", "10000", "11111"}},
        {'F', {"11111", "10000", "10000", "11110", "10000", "10000", "10000"}},
        {'G', {"01111", "10000", "10000", "10011", "10001", "10001", "01110"}},
        {'H', {"10001", "10001", "10001", "11111", "10001", "10001", "10001"}},
        {'I', {"11111", "00100", "00100", "00100", "00100", "00100", "11111"}},
        {'J', {"00111", "00010", "00010", "00010", "10010", "10010", "01100"}},
        {'K', {"10001", "10010", "10100", "11000", "10100", "10010", "10001"}},
        {'L', {"10000", "10000", "10000", "10000", "10000", "10000", "11111"}},
        {'M', {"10001", "11011", "10101", "10101", "10001", "10001", "10001"}},
        {'N', {"10001", "11001", "10101", "10011", "10001", "10001", "10001"}},
        {'O', {"01110", "10001", "10001", "10001", "10001", "10001", "01110"}},
        {'P', {"11110", "10001", "10001", "11110", "10000", "10000", "10000"}},
        {'Q', {"01110", "10001", "10001", "10001", "10101", "10010", "01101"}},
        {'R', {"11110", "10001", "10001", "11110", "10100", "10010", "10001"}},
        {'S', {"01111", "10000", "10000", "01110", "00001", "00001", "11110"}},
        {'T', {"11111", "00100", "00100", "00100", "00100", "00100", "00100"}},
        {'U', {"10001", "10001", "10001", "10001", "10001", "10001", "01110"}},
        {'V', {"10001", "10001", "10001", "10001", "10001", "01010", "00100"}},
        {'W', {"10001", "10001", "10001", "10101", "10101", "10101", "01010"}},
        {'X', {"10001", "10001", "01010", "00100", "01010", "10001", "10001"}},
        {'Y', {"10001", "10001", "01010", "00100", "00100", "00100", "00100"}},
        {'Z', {"11111", "00001", "00010", "00100", "01000", "10000", "11111"}},
        {'0', {"01110", "10001", "10011", "10101", "11001", "10001", "01110"}},
        {'1', {"00100", "01100", "00100", "00100", "00100", "00100", "01110"}},
        {'2', {"01110", "10001", "00001", "00010", "00100", "01000", "11111"}},
        {'3', {"11110", "00001", "00001", "01110", "00001", "00001", "11110"}},
        {'4', {"00010", "00110", "01010", "10010", "11111", "00010", "00010"}},
        {'5', {"11111", "10000", "10000", "11110", "00001", "00001", "11110"}},
        {'6', {"01110", "10000", "10000", "11110", "10001", "10001", "01110"}},
        {'7', {"11111", "00001", "00010", "00100", "01000", "01000", "01000"}},
        {'8', {"01110", "10001", "10001", "01110", "10001", "10001", "01110"}},
        {'9', {"01110", "10001", "10001", "01111", "00001", "00001", "01110"}},
        {' ', {"00000", "00000", "00000", "00000", "00000", "00000", "00000"}},
        {'.', {"00000", "00000", "00000", "00000", "00000", "01100", "01100"}},
        {',', {"00000", "00000", "00000", "00000", "01100", "00100", "01000"}},
        {':', {"00000", "01100", "01100", "00000", "01100", "01100", "00000"}},
        {';', {"00000", "01100", "01100", "00000", "01100", "00100", "01000"}},
        {'-', {"00000", "00000", "00000", "11111", "00000", "00000", "00000"}},
        {'_', {"00000", "00000", "00000", "00000", "00000", "00000", "11111"}},
        {'+', {"00000", "00100", "00100", "11111", "00100", "00100", "00000"}},
        {'/', {"00001", "00010", "00010", "00100", "01000", "01000", "10000"}},
        {'\\', {"10000", "01000", "01000", "00100", "00010", "00010", "00001"}},
        {'|', {"00100", "00100", "00100", "00100", "00100", "00100", "00100"}},
        {'(', {"00010", "00100", "01000", "01000", "01000", "00100", "00010"}},
        {')', {"01000", "00100", "00010", "00010", "00010", "00100", "01000"}},
        {'[', {"01110", "01000", "01000", "01000", "01000", "01000", "01110"}},
        {']', {"01110", "00010", "00010", "00010", "00010", "00010", "01110"}},
        {'<', {"00010", "00100", "01000", "10000", "01000", "00100", "00010"}},
        {'>', {"01000", "00100", "00010", "00001", "00010", "00100", "01000"}},
        {'=', {"00000", "11111", "00000", "11111", "00000", "00000", "00000"}},
        {'?', {"01110", "10001", "00001", "00010", "00100", "00000", "00100"}},
        {'!', {"00100", "00100", "00100", "00100", "00100", "00000", "00100"}},
        {'%', {"11001", "11010", "00010", "00100", "01000", "01011", "10011"}},
        {'#', {"01010", "01010", "11111", "01010", "11111", "01010", "01010"}},
        {'*', {"00000", "10101", "01110", "11111", "01110", "10101", "00000"}},
        {'@', {"01110", "10001", "10111", "10101", "10111", "10000", "01110"}},
        {'\'', {"01100", "00100", "01000", "00000", "00000", "00000", "00000"}},
        {'\"', {"01010", "01010", "01010", "00000", "00000", "00000", "00000"}},
    };
    if (ch >= 'a' && ch <= 'z')
        ch = static_cast<char>(ch - 'a' + 'A');
    auto it = table.find(ch);
    if (it == table.end())
        return (ch == ' ') ? blank : unknown;
    return it->second.data();
}

// Fallback bitmap renderer (used only if no system TrueType font could be loaded).
void text_bitmap(SDL_Renderer* r, int x, int y, const std::string& s, int scale, Color c) {
    set_color(r, c);
    int cx = x;
    const int char_w = 6 * scale;
    for (char ch : s) {
        if (ch == '\n') {
            y += 9 * scale;
            cx = x;
            continue;
        }
        const char* const* g = glyph(ch);
        for (int gy = 0; gy < 7; ++gy) {
            for (int gx = 0; gx < 5; ++gx) {
                if (g[gy][gx] == '1') {
                    SDL_Rect px{cx + gx * scale, y + gy * scale, scale, scale};
                    SDL_RenderFillRect(r, &px);
                }
            }
        }
        cx += char_w;
    }
}

// ---- real anti-aliased text via stb_truetype (a system font, like the Python beta) ----
// One system TTF is loaded at startup; each drawn line is rasterised once into a cached
// SDL texture (colour baked in). If no font is found, everything falls back to the bitmap
// font above, so text always renders on every platform.
struct Font {
    bool ok = false;
    std::vector<unsigned char> data;
    stbtt_fontinfo info{};
    int ascent = 0, descent = 0, line_gap = 0;
    SDL_Renderer* ren = nullptr;
    struct Item {
        SDL_Texture* tex = nullptr;
        int w = 0, h = 0;
    };
    std::map<std::string, Item> cache;

    // Map the old bitmap "scale" (1 small … 3 title) to a pixel height, tuned to the
    // Python beta's font sizes (body ~14-20, titles ~24-30).
    static int px_for(int scale) {
        switch (scale) {
            case 1:
                return 14;
            case 2:
                return 20;
            default:
                return 30; // 3 = titles
        }
    }

    bool load(SDL_Renderer* r) {
        ren = r;
        static const char* const candidates[] = {
#if defined(_WIN32)
            "C:/Windows/Fonts/segoeui.ttf",
            "C:/Windows/Fonts/arial.ttf",
            "C:/Windows/Fonts/tahoma.ttf",
            "C:/Windows/Fonts/verdana.ttf",
#elif defined(__APPLE__)
            "/System/Library/Fonts/SFNS.ttf",   "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/Library/Fonts/Arial.ttf",         "/System/Library/Fonts/Helvetica.ttc",
            "/System/Library/Fonts/Geneva.ttf",
#else
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/TTF/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/usr/share/fonts/liberation-sans/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
            "/usr/share/fonts/noto/NotoSans-Regular.ttf",
            "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
#endif
        };
        for (const char* path : candidates) {
            std::FILE* f = std::fopen(path, "rb");
            if (!f)
                continue;
            std::fseek(f, 0, SEEK_END);
            const long n = std::ftell(f);
            std::fseek(f, 0, SEEK_SET);
            if (n <= 0) {
                std::fclose(f);
                continue;
            }
            data.resize(static_cast<std::size_t>(n));
            const std::size_t rd = std::fread(data.data(), 1, data.size(), f);
            std::fclose(f);
            if (rd != data.size()) {
                data.clear();
                continue;
            }
            const int off = stbtt_GetFontOffsetForIndex(data.data(), 0);
            if (off < 0 || !stbtt_InitFont(&info, data.data(), off)) {
                data.clear();
                continue;
            }
            stbtt_GetFontVMetrics(&info, &ascent, &descent, &line_gap);
            ok = true;
            applog::info(std::string("[gui] font: ") + path);
            return true;
        }
        applog::info("[gui] no system TTF found — using the built-in bitmap font");
        return false;
    }

    void clear() {
        for (auto& kv : cache)
            if (kv.second.tex)
                SDL_DestroyTexture(kv.second.tex);
        cache.clear();
    }

    int line_advance(int px) const {
        const float sc = stbtt_ScaleForPixelHeight(&info, static_cast<float>(px));
        return static_cast<int>((ascent - descent + line_gap) * sc + 0.5f);
    }

    int measure_w(const std::string& s, int px) const {
        const float sc = stbtt_ScaleForPixelHeight(&info, static_cast<float>(px));
        float pen = 0.0f;
        for (std::size_t i = 0; i < s.size(); ++i) {
            int adv = 0, lsb = 0;
            stbtt_GetCodepointHMetrics(&info, static_cast<unsigned char>(s[i]), &adv, &lsb);
            pen += adv * sc;
            if (i + 1 < s.size())
                pen += stbtt_GetCodepointKernAdvance(&info, static_cast<unsigned char>(s[i]),
                                                     static_cast<unsigned char>(s[i + 1])) *
                       sc;
        }
        return static_cast<int>(pen + 0.5f);
    }

    // Rasterise ONE line (no '\n') to a cached, colour-baked texture.
    Item* line_item(const std::string& s, int px, Color c) {
        char head[24];
        std::snprintf(head, sizeof(head), "%d|%02x%02x%02x|", px, c.r, c.g, c.b);
        const std::string key = head + s;
        auto it = cache.find(key);
        if (it != cache.end())
            return &it->second;
        if (cache.size() > 2048)
            clear(); // bound memory; rare rebuild spike

        const float sc = stbtt_ScaleForPixelHeight(&info, static_cast<float>(px));
        const int baseline = static_cast<int>(ascent * sc + 0.5f);
        const int H = static_cast<int>((ascent - descent) * sc + 0.5f) + 2;
        float pen = 0.0f;
        for (std::size_t i = 0; i < s.size(); ++i) {
            int adv = 0, lsb = 0;
            stbtt_GetCodepointHMetrics(&info, static_cast<unsigned char>(s[i]), &adv, &lsb);
            pen += adv * sc;
            if (i + 1 < s.size())
                pen += stbtt_GetCodepointKernAdvance(&info, static_cast<unsigned char>(s[i]),
                                                     static_cast<unsigned char>(s[i + 1])) *
                       sc;
        }
        const int W = std::max(1, static_cast<int>(pen + 2.0f));
        std::vector<unsigned char> alpha(static_cast<std::size_t>(W) * H, 0);
        pen = 0.0f;
        for (std::size_t i = 0; i < s.size(); ++i) {
            const unsigned char ch = static_cast<unsigned char>(s[i]);
            int adv = 0, lsb = 0;
            stbtt_GetCodepointHMetrics(&info, ch, &adv, &lsb);
            const float xshift = pen - std::floor(pen);
            int x0, y0, x1, y1;
            stbtt_GetCodepointBitmapBoxSubpixel(&info, ch, sc, sc, xshift, 0, &x0, &y0, &x1, &y1);
            const int gw = x1 - x0, gh = y1 - y0;
            if (gw > 0 && gh > 0) {
                std::vector<unsigned char> gb(static_cast<std::size_t>(gw) * gh);
                stbtt_MakeCodepointBitmapSubpixel(&info, gb.data(), gw, gh, gw, sc, sc, xshift, 0,
                                                  ch);
                const int ox = static_cast<int>(pen) + x0;
                const int oy = baseline + y0;
                for (int yy = 0; yy < gh; ++yy) {
                    const int dy = oy + yy;
                    if (dy < 0 || dy >= H)
                        continue;
                    for (int xx = 0; xx < gw; ++xx) {
                        const int dx = ox + xx;
                        if (dx < 0 || dx >= W)
                            continue;
                        const unsigned char v = gb[static_cast<std::size_t>(yy) * gw + xx];
                        unsigned char& d = alpha[static_cast<std::size_t>(dy) * W + dx];
                        if (v > d)
                            d = v;
                    }
                }
            }
            pen += adv * sc;
            if (i + 1 < s.size())
                pen +=
                    stbtt_GetCodepointKernAdvance(&info, ch, static_cast<unsigned char>(s[i + 1])) *
                    sc;
        }
        std::vector<std::uint32_t> pix(static_cast<std::size_t>(W) * H);
        for (std::size_t i = 0; i < pix.size(); ++i)
            pix[i] = (static_cast<std::uint32_t>(alpha[i]) << 24) |
                     (static_cast<std::uint32_t>(c.b) << 16) |
                     (static_cast<std::uint32_t>(c.g) << 8) | static_cast<std::uint32_t>(c.r);
        Item item;
        SDL_Texture* tex =
            SDL_CreateTexture(ren, SDL_PIXELFORMAT_ABGR8888, SDL_TEXTUREACCESS_STATIC, W, H);
        if (tex) {
            SDL_UpdateTexture(tex, nullptr, pix.data(), W * 4);
            SDL_SetTextureBlendMode(tex, SDL_BLENDMODE_BLEND);
            item.tex = tex;
            item.w = W;
            item.h = H;
        }
        return &cache.emplace(key, item).first->second;
    }
};

Font g_font;

void text(SDL_Renderer* r, int x, int y, const std::string& s, int scale, Color c) {
    if (!g_font.ok) {
        text_bitmap(r, x, y, s, scale, c);
        return;
    }
    const int px = Font::px_for(scale);
    const int adv = g_font.line_advance(px);
    int ly = y;
    std::size_t start = 0;
    while (true) {
        const std::size_t nl = s.find('\n', start);
        const std::string line =
            s.substr(start, nl == std::string::npos ? std::string::npos : nl - start);
        if (!line.empty()) {
            Font::Item* it = g_font.line_item(line, px, c);
            if (it && it->tex) {
                SDL_Rect dst{x, ly, it->w, it->h};
                SDL_RenderCopy(r, it->tex, nullptr, &dst);
            }
        }
        if (nl == std::string::npos)
            break;
        ly += adv;
        start = nl + 1;
    }
}

void text_center(SDL_Renderer* r, const SDL_Rect& rc, const std::string& s, int scale, Color c) {
    if (!g_font.ok) {
        const int w = static_cast<int>(s.size()) * 6 * scale - scale;
        const int h = 7 * scale;
        text_bitmap(r, rc.x + (rc.w - w) / 2, rc.y + (rc.h - h) / 2, s, scale, c);
        return;
    }
    Font::Item* it = g_font.line_item(s, Font::px_for(scale), c);
    if (!it || !it->tex)
        return;
    SDL_Rect dst{rc.x + (rc.w - it->w) / 2, rc.y + (rc.h - it->h) / 2, it->w, it->h};
    SDL_RenderCopy(r, it->tex, nullptr, &dst);
}

// Pixel width of a single-line string at the given scale (for chip / badge sizing).
int text_w(const std::string& s, int scale) {
    return g_font.ok ? g_font.measure_w(s, Font::px_for(scale))
                     : static_cast<int>(s.size()) * 6 * scale;
}

bool inside(const SDL_Rect& r, int x, int y) {
    return x >= r.x && y >= r.y && x < r.x + r.w && y < r.y + r.h;
}

struct Button {
    SDL_Rect rect{};
    std::string label;
    bool primary = false;
    bool enabled = true;
    std::function<void()> click;

    void draw(SDL_Renderer* r, int mx, int my) const {
        Color base = primary ? ACCENT : PANEL_HI;
        if (!enabled)
            base = PANEL;
        else if (inside(rect, mx, my))
            base = primary ? ACCENT_HI : Color{52, 57, 72, 255};
        fill_round(r, rect, 8, base);
        outline_round(r, rect, 8, primary ? ACCENT_HI : Color{70, 76, 92, 255});
        text_center(r, rect, label, 2, primary ? Color{12, 16, 22, 255} : TEXT);
    }

    bool handle(const SDL_Event& e) const {
        if (!enabled || e.type != SDL_MOUSEBUTTONDOWN || e.button.button != SDL_BUTTON_LEFT)
            return false;
        if (!inside(rect, e.button.x, e.button.y))
            return false;
        if (click)
            click();
        return true;
    }
};

struct TextInput {
    SDL_Rect rect{};
    std::string value;
    std::string placeholder;
    bool focused = false;
    // Wi-Fi passphrase entry: show dots, and offer a reveal toggle next to the field
    // (a typo in a WPA2 key is otherwise indistinguishable from a wrong password).
    bool password = false;
    bool reveal = false;

    void draw(SDL_Renderer* r) const {
        fill_round(r, rect, 6, Color{14, 16, 21, 255});
        outline_round(r, rect, 6, focused ? ACCENT : PANEL_HI);
        std::string shown = value;
        if (password && !reveal)
            shown.assign(value.size(), '*');
        if (shown.empty())
            shown = placeholder;
        if (focused && (SDL_GetTicks() / 500) % 2 == 0)
            shown += "|";
        text(r, rect.x + 10, rect.y + 12, shown.substr(0, 80), 2, value.empty() ? MUTED : TEXT);
    }

    bool handle(const SDL_Event& e) {
        if (e.type == SDL_MOUSEBUTTONDOWN && e.button.button == SDL_BUTTON_LEFT) {
            focused = inside(rect, e.button.x, e.button.y);
            return focused;
        }
        if (!focused)
            return false;
        if (e.type == SDL_TEXTINPUT) {
            value += e.text.text;
            return true;
        }
        if (e.type == SDL_KEYDOWN && e.key.keysym.sym == SDLK_BACKSPACE) {
            if (!value.empty())
                value.pop_back();
            return true;
        }
        return false;
    }
};

struct ConnectionSpec {
    std::string mode;
    std::string host;
    int port = netfind::BRIDGE_PORT;
    std::string serial;
};

std::unique_ptr<Transport> make_transport(const ConnectionSpec& s) {
    if (s.mode == "sim")
        return std::make_unique<LogTransport>(true);
    if (s.mode == "serial")
        return std::make_unique<SerialTransport>(s.serial);
    return std::make_unique<CompositeTransport>(std::make_unique<NetTransport>(s.host, s.port));
}

struct ScreenBase {
    SDL_Window* win = nullptr;
    SDL_Renderer* ren = nullptr;
    bool done = false;
    int result = 0;
    int mx = 0, my = 0;

    void clear() {
        set_color(ren, BG);
        SDL_RenderClear(ren);
    }

    void mouse() {
        SDL_GetMouseState(&mx, &my);
    }
};

enum class MenuResult { Pi, Serial, Sim, Quit };

struct UpdateUi {
    enum class State { Idle, Checking, Ready, Error };
    std::atomic<State> state{State::Idle};
    std::string msg;
    std::string installer;
    std::mutex mu;
    std::thread worker;

    ~UpdateUi() {
        if (worker.joinable())
            worker.join();
    }

    void set(State s, std::string m) {
        std::lock_guard<std::mutex> lk(mu);
        state.store(s);
        msg = std::move(m);
    }

    std::string message() {
        std::lock_guard<std::mutex> lk(mu);
        return msg;
    }

    void check_async() {
        if (state.load() == State::Checking)
            return;
        if (worker.joinable())
            worker.join();
        set(State::Checking, "Checking GitHub releases...");
        worker = std::thread([this] {
            std::string err;
            auto rel = updater::check(err);
            if (!rel) {
                set(State::Error, err.empty() ? "No update found." : err);
                return;
            }
            auto local = updater::download(*rel, err);
            if (!local) {
                set(State::Error, err.empty() ? "Download failed." : err);
                return;
            }
            {
                std::lock_guard<std::mutex> lk(mu);
                installer = *local;
                msg = "Found new version: " + rel->tag + ". Downloaded installer. Start?";
            }
            state.store(State::Ready);
        });
    }
};

MenuResult menu_screen(SDL_Window* win, SDL_Renderer* r) {
    UpdateUi update;
    bool quit = false;
    MenuResult out = MenuResult::Quit;
    SDL_StartTextInput();
    while (!quit) {
        SDL_Event e;
        int w, h;
        SDL_GetWindowSize(win, &w, &h);
        const int bw = 390, bh = 52, gap = 14;
        const int cx = w / 2 - bw / 2;
        const int y0 = h / 2 - 135;
        std::vector<Button> buttons;
        buttons.push_back({{cx, y0, bw, bh}, "Connect via Raspberry Pi", true, true, [&] {
                               out = MenuResult::Pi;
                               quit = true;
                           }});
        buttons.push_back({{cx, y0 + (bh + gap), bw, bh}, "Connect via serial", false, true, [&] {
                               out = MenuResult::Serial;
                               quit = true;
                           }});
        buttons.push_back({{cx, y0 + 2 * (bh + gap), bw, bh}, "Simulator", false, true, [&] {
                               out = MenuResult::Sim;
                               quit = true;
                           }});
        buttons.push_back(
            {{cx, y0 + 3 * (bh + gap), bw, bh}, "Check for updates", false, true, [&] {
                 update.check_async();
             }});
        buttons.push_back({{cx, y0 + 4 * (bh + gap), bw, bh}, "Quit", false, true, [&] {
                               out = MenuResult::Quit;
                               quit = true;
                           }});

        std::vector<Button> update_buttons;
        if (update.state.load() == UpdateUi::State::Ready) {
            update_buttons.push_back({{cx, y0 + 5 * (bh + gap), 185, 44}, "Start", true, true, [&] {
                                          std::string err;
                                          std::string installer;
                                          {
                                              std::lock_guard<std::mutex> lk(update.mu);
                                              installer = update.installer;
                                          }
                                          if (updater::install_and_relaunch(installer, err)) {
                                              out = MenuResult::Quit;
                                              quit = true;
                                          } else {
                                              update.set(UpdateUi::State::Error, err);
                                          }
                                      }});
            update_buttons.push_back(
                {{cx + 205, y0 + 5 * (bh + gap), 185, 44}, "No", false, true, [&] {
                     std::string installer;
                     {
                         std::lock_guard<std::mutex> lk(update.mu);
                         installer = update.installer;
                     }
                     updater::discard(installer);
                     update.set(UpdateUi::State::Idle, "Update discarded.");
                 }});
        }

        while (SDL_PollEvent(&e)) {
            if (e.type == SDL_QUIT) {
                out = MenuResult::Quit;
                quit = true;
            } else if (e.type == SDL_KEYDOWN && e.key.keysym.sym == SDLK_ESCAPE) {
                out = MenuResult::Quit;
                quit = true;
            }
            for (const auto& b : buttons)
                if (b.handle(e))
                    break;
            for (const auto& b : update_buttons)
                if (b.handle(e))
                    break;
        }

        int mx, my;
        SDL_GetMouseState(&mx, &my);
        set_color(r, BG);
        SDL_RenderClear(r);
        text(r, 40, 34, "DJI Mavic Mini 1 - PC control", 3, TEXT);
        text(r, 40, 72, "Control the drone from your PC. Pick how to connect.", 2, MUTED);
        for (const auto& b : buttons)
            b.draw(r, mx, my);
        for (const auto& b : update_buttons)
            b.draw(r, mx, my);
        const std::string m = update.message();
        if (!m.empty()) {
            Color c = update.state.load() == UpdateUi::State::Error ? WARN : MUTED;
            text(r, 40, h - 76, m.substr(0, 130), 2, c);
        }
        text(r, 40, h - 40, "Tip: press F1 in flight for the full list of controls.", 1, MUTED);
        SDL_RenderPresent(r);
        SDL_Delay(16);
    }
    SDL_StopTextInput();
    return out;
}

std::optional<ConnectionSpec> serial_screen(SDL_Window* win, SDL_Renderer* r,
                                            const std::string& def) {
    TextInput input{{40, 140, 560, 46}, def, "COM3 OR /DEV/TTYACM0", true};
    SDL_StartTextInput();
    bool done = false;
    std::optional<ConnectionSpec> result;
    while (!done) {
        SDL_Event e;
        int w, h;
        SDL_GetWindowSize(win, &w, &h);
        Button connect{{40, 210, 200, 46}, "Connect", true, true, [&] {
                           if (!input.value.empty())
                               result = ConnectionSpec{"serial", "", 0, input.value};
                           done = true;
                       }};
        Button back{{260, 210, 150, 46}, "Back", false, true, [&] { done = true; }};
        while (SDL_PollEvent(&e)) {
            if (e.type == SDL_QUIT) {
                result = ConnectionSpec{"quit"};
                done = true;
            } else if (e.type == SDL_KEYDOWN && e.key.keysym.sym == SDLK_ESCAPE) {
                done = true;
            } else if (e.type == SDL_KEYDOWN && e.key.keysym.sym == SDLK_RETURN) {
                if (!input.value.empty())
                    result = ConnectionSpec{"serial", "", 0, input.value};
                done = true;
            }
            input.handle(e);
            connect.handle(e);
            back.handle(e);
        }
        int mx, my;
        SDL_GetMouseState(&mx, &my);
        set_color(r, BG);
        SDL_RenderClear(r);
        text(r, 40, 40, "CONNECT VIA SERIAL", 3, TEXT);
        text(r, 40, 88, "Enter the RC/drone serial port.", 2, MUTED);
        input.draw(r);
        connect.draw(r, mx, my);
        back.draw(r, mx, my);
        SDL_RenderPresent(r);
        SDL_Delay(16);
    }
    SDL_StopTextInput();
    return result;
}

// ------------------------------------------------------------------ Pi Wi-Fi screen
// Connect the Raspberry Pi to a Wi-Fi network from the PC. The Pi has one radio: it
// holds the access point this PC is joined to AND acts as the uplink client, so all of
// the scanning and joining happens ON THE PI (netctl's HTTP API) — the PC never touches
// its own adapter here. Joining retunes the AP, which briefly drops this PC; the screen
// waits for the Pi to answer again rather than reporting a failure.
//
// Every platform runs the identical code path: the work is HTTP to the Pi, so there is
// no netsh/nmcli/airport branch to get wrong.
struct WifiUi {
    enum class State { Idle, Scanning, Connecting, Waiting, Done, Error };

    std::string host;
    std::mutex mu;
    std::atomic<State> state{State::Idle};
    std::vector<netfind::WifiNet> nets;
    std::optional<netfind::PiNetStatus> status;
    std::string msg;
    int selected = -1;
    int scroll = 0;
    std::thread worker;

    explicit WifiUi(std::string h) : host(std::move(h)) {}

    ~WifiUi() {
        if (worker.joinable())
            worker.join();
    }

    bool busy() const {
        const State s = state.load();
        return s == State::Scanning || s == State::Connecting || s == State::Waiting;
    }

    void set(State s, std::string m) {
        std::lock_guard<std::mutex> lk(mu);
        state.store(s);
        msg = std::move(m);
    }

    std::string message() {
        std::lock_guard<std::mutex> lk(mu);
        return msg;
    }

    std::vector<netfind::WifiNet> list() {
        std::lock_guard<std::mutex> lk(mu);
        return nets;
    }

    std::optional<netfind::PiNetStatus> stat() {
        std::lock_guard<std::mutex> lk(mu);
        return status;
    }

    // Every action runs on its own thread so the SDL loop keeps rendering: a scan takes
    // ~3 s on the Pi and a join up to a minute, and a frozen window mid-setup is exactly
    // what the queued-command work in Client was meant to end.
    // NOTE: callers (refresh/connect/disconnect) set a busy state before calling spawn,
    // and the UI buttons prevent concurrent calls, so no guard is needed here.
    void spawn(std::function<void()> job) {
        if (worker.joinable())
            worker.join();
        worker = std::thread(std::move(job));
    }

    void refresh() {
        set(State::Scanning, "Asking the Pi to scan for networks...");
        spawn([this] {
            auto st = netfind::pi_status(host);
            auto found = netfind::pi_scan_wifi(host);
            {
                std::lock_guard<std::mutex> lk(mu);
                status = st;
                nets = found;
                if (selected >= static_cast<int>(nets.size()))
                    selected = -1;
            }
            if (found.empty()) {
                set(State::Error, st ? "The Pi sees no networks. Move it closer to the router "
                                       "and scan again."
                                     : "The Pi stopped answering on port 9911.");
                return;
            }
            std::string m =
                "Pick a network for the Pi. " + std::to_string(found.size()) + " in range.";
            if (st && st->internet)
                m += " It already has internet" +
                     (st->uplink_name.empty() ? std::string() : " via '" + st->uplink_name + "'") +
                     ".";
            set(State::Done, m);
        });
    }

    void connect(const std::string& ssid, const std::string& psk) {
        set(State::Connecting, "Connecting the Pi to '" + ssid + "'...");
        spawn([this, ssid, psk] {
            auto res = netfind::pi_connect_wifi(host, ssid, psk);
            // Pi's async dji-ap restart (for channel retune) may tear the TCP connection
            // before the response arrives — the client then sees "no answer" even on a
            // successful join. Always probe /status after the join and treat that as the
            // truth; res.ok is only a hint for the error message.
            set(State::Waiting, "Waiting for the Pi access point to come back...");
            const bool back = netfind::wait_for_pi(host);
            if (!back) {
                set(State::Error, "Lost the Pi after the channel change. "
                                  "Re-join the 'PI_DJI_LINK-*' network on this PC, "
                                  "then scan again.");
                return;
            }
            auto st = netfind::pi_status(host);
            {
                std::lock_guard<std::mutex> lk(mu);
                status = st;
            }
            if (st && st->internet) {
                set(State::Done, "The Pi is on '" + ssid + "' and has internet.");
                return;
            }
            // Pi answered but reports no internet. Check whether it joined the network
            // at all (uplink_name matches) — that distinguishes a captive portal / no-
            // route situation from a wrong password.
            const bool joined =
                st && (st->uplink_name == ssid || st->uplink_name.find(ssid) != std::string::npos);
            if (joined) {
                set(State::Error, "The Pi joined '" + ssid +
                                      "' but has no internet. Captive portal or no route "
                                      "out — nothing to do here.");
            } else {
                const bool no_answer = res.output.find("did not answer") != std::string::npos;
                set(State::Error, no_answer ? "The Pi did not answer. It may still be reconnecting "
                                              "— wait a moment and press 'Scan again'."
                                            : (res.output.empty() ? "Could not join '" + ssid +
                                                                        "'. Wrong password?"
                                                                  : res.output.substr(0, 160)));
            }
        });
    }

    void disconnect() {
        set(State::Connecting, "Disconnecting the Pi's uplink...");
        spawn([this] {
            auto res = netfind::pi_disconnect_wifi(host);
            netfind::wait_for_pi(host, 20.0);
            auto st = netfind::pi_status(host);
            {
                std::lock_guard<std::mutex> lk(mu);
                status = st;
            }
            set(res.ok ? State::Done : State::Error,
                res.ok ? "The Pi's uplink is down. Its access point is still up."
                       : "Disconnect failed: " + res.output.substr(0, 140));
        });
    }
};

void wifi_screen(SDL_Window* win, SDL_Renderer* r, const std::string& host) {
    WifiUi ui(host);
    TextInput pw{{0, 0, 420, 46}, "", "WI-FI PASSWORD", false, true, false};
    ui.refresh();
    bool done = false;
    SDL_StartTextInput();
    while (!done) {
        int w, h;
        SDL_GetWindowSize(win, &w, &h);
        auto nets = ui.list();
        const int rows = std::max(1, std::min<int>(7, static_cast<int>(nets.size())));
        const SDL_Rect list{40, 150, w - 80, rows * 34 + 12};
        const int form_y = list.y + list.h + 22;
        pw.rect = {40, form_y, std::min(420, w - 80), 46};

        const bool have_sel = ui.selected >= 0 && ui.selected < static_cast<int>(nets.size());
        const bool needs_pw = have_sel && !nets[ui.selected].open();
        std::vector<Button> buttons;
        buttons.push_back({{40, form_y + 62, 220, 46},
                           "Connect the Pi",
                           true,
                           !ui.busy() && have_sel && (!needs_pw || !pw.value.empty()),
                           [&] {
                               if (have_sel)
                                   ui.connect(nets[ui.selected].ssid, pw.value);
                           }});
        buttons.push_back(
            {{275, form_y + 62, 150, 46}, "Scan again", false, !ui.busy(), [&] { ui.refresh(); }});
        auto st = ui.stat();
        buttons.push_back({{440, form_y + 62, 190, 46},
                           "Disconnect uplink",
                           false,
                           !ui.busy() && st.has_value() && !st->uplink_name.empty(),
                           [&] { ui.disconnect(); }});
        buttons.push_back(
            {{w - 200, h - 80, 160, 46}, "Back", false, !ui.busy(), [&] { done = true; }});
        Button reveal{{pw.rect.x + pw.rect.w + 12, form_y, 110, 46},
                      pw.reveal ? "Hide" : "Show",
                      false,
                      true,
                      [&] { pw.reveal = !pw.reveal; }};

        SDL_Event e;
        while (SDL_PollEvent(&e)) {
            if (e.type == SDL_QUIT || (e.type == SDL_KEYDOWN && e.key.keysym.sym == SDLK_ESCAPE)) {
                if (!ui.busy())
                    done = true;
            } else if (e.type == SDL_MOUSEWHEEL) {
                ui.scroll = std::max(
                    0, std::min<int>(ui.scroll - e.wheel.y,
                                     std::max<int>(0, static_cast<int>(nets.size()) - rows)));
            } else if (e.type == SDL_MOUSEBUTTONDOWN && e.button.button == SDL_BUTTON_LEFT &&
                       inside(list, e.button.x, e.button.y)) {
                const int idx = ui.scroll + (e.button.y - list.y - 6) / 34;
                if (idx >= 0 && idx < static_cast<int>(nets.size())) {
                    ui.selected = idx;
                    pw.value.clear();
                    pw.focused = !nets[idx].open();
                }
            } else if (e.type == SDL_KEYDOWN && e.key.keysym.sym == SDLK_RETURN && have_sel &&
                       !ui.busy() && (!needs_pw || !pw.value.empty())) {
                ui.connect(nets[ui.selected].ssid, pw.value);
            }
            pw.handle(e);
            reveal.handle(e);
            for (const auto& b : buttons)
                if (b.handle(e))
                    break;
        }

        int mx, my;
        SDL_GetMouseState(&mx, &my);
        set_color(r, BG);
        SDL_RenderClear(r);
        text(r, 40, 40, "CONNECT THE PI TO WI-FI", 3, TEXT);
        text(r, 40, 88,
             "The Pi scans and joins with its own radio. Its access point stays up, so this "
             "PC keeps the link.",
             2, MUTED);
        if (st) {
            std::string line =
                "Pi access point: " + (st->ap_ssid.empty() ? std::string("unknown") : st->ap_ssid) +
                (st->ap_active ? " (up)" : " (down)") +
                "   Uplink: " + (st->uplink_name.empty() ? std::string("none") : st->uplink_name) +
                "   Internet: " + (st->internet ? "yes" : "no");
            text(r, 40, 118, line, 1, st->internet ? GOOD : MUTED);
        }

        fill_round(r, list, 8, PANEL);
        outline_round(r, list, 8, PANEL_HI);
        for (int i = 0; i < rows; ++i) {
            const int idx = ui.scroll + i;
            if (idx >= static_cast<int>(nets.size()))
                break;
            const auto& n = nets[idx];
            const SDL_Rect row{list.x + 6, list.y + 6 + i * 34, list.w - 12, 32};
            if (idx == ui.selected)
                fill_round(r, row, 6, ACCENT);
            else if (inside(row, mx, my))
                fill_round(r, row, 6, PANEL_HI);
            const Color fg = idx == ui.selected ? Color{12, 16, 22, 255} : TEXT;
            char sig[16];
            std::snprintf(sig, sizeof(sig), "%3d%%", n.signal);
            text(r, row.x + 10, row.y + 8, sig, 2, fg);
            text(r, row.x + 76, row.y + 8, n.open() ? "open" : n.security, 2,
                 idx == ui.selected ? fg : MUTED);
            text(r, row.x + 220, row.y + 8, n.ssid.substr(0, 46), 2, fg);
            if (n.in_use)
                text(r, row.x + row.w - 90, row.y + 8, "in use", 2, idx == ui.selected ? fg : GOOD);
        }
        if (nets.empty()) {
            text(r, list.x + 16, list.y + 14,
                 ui.busy() ? "Scanning..." : "No networks. Press \"Scan again\".", 2, MUTED);
        }

        if (have_sel && !needs_pw) {
            text(r, 40, form_y + 14, "'" + nets[ui.selected].ssid + "' is open - no password.", 2,
                 MUTED);
        } else {
            pw.draw(r);
            reveal.draw(r, mx, my);
        }
        for (const auto& b : buttons)
            b.draw(r, mx, my);

        const std::string m = ui.message();
        if (!m.empty()) {
            const auto s = ui.state.load();
            Color c = s == WifiUi::State::Error ? WARN : s == WifiUi::State::Done ? GOOD : ACCENT;
            text(r, 40, form_y + 126, m.substr(0, 150), 2, c);
        }
        text(r, 40, h - 74,
             "Joining a network retunes the Pi's access point to that channel (one radio), so "
             "this PC may reconnect on its own - that is normal.",
             1, MUTED);
        SDL_RenderPresent(r);
        SDL_Delay(16);
    }
    SDL_StopTextInput();
}

std::optional<ConnectionSpec> discovery_screen(SDL_Window* win, SDL_Renderer* r,
                                               const std::optional<std::string>& saved_host) {
    std::atomic<bool> scanning{true};
    netfind::DiscoverResult disc;
    std::thread worker([&] {
        disc = netfind::discover(saved_host);
        scanning.store(false);
    });
    bool done = false;
    std::optional<ConnectionSpec> result;
    while (!done) {
        SDL_Event e;
        int w, h;
        SDL_GetWindowSize(win, &w, &h);
        Button start{{w - 270, h - 110, 230, 48},
                     "Start flying",
                     true,
                     !scanning.load() && disc.host.has_value(),
                     [&] {
                         if (disc.host) {
                             result = ConnectionSpec{"pi", *disc.host, netfind::BRIDGE_PORT, ""};
                             done = true;
                         }
                     }};
        Button retry{{w - 270, h - 170, 230, 44}, "Retry", false, !scanning.load(), [&] {
                         if (worker.joinable())
                             worker.join();
                         scanning.store(true);
                         disc = {};
                         worker = std::thread([&] {
                             disc = netfind::discover(saved_host);
                             scanning.store(false);
                         });
                     }};
        Button back{{40, h - 110, 160, 44}, "Back", false, true, [&] { done = true; }};
        // Available as soon as the Pi answers, not only when it lacks internet: the user
        // may want to move it to another network, or drop the uplink before flying.
        Button wifi{{220, h - 110, 240, 44},
                    "Pi Wi-Fi setup",
                    false,
                    !scanning.load() && disc.host.has_value(),
                    [&] {
                        if (disc.host)
                            wifi_screen(win, r, *disc.host);
                    }};
        while (SDL_PollEvent(&e)) {
            if (e.type == SDL_QUIT) {
                result = ConnectionSpec{"quit"};
                done = true;
            } else if (e.type == SDL_KEYDOWN && e.key.keysym.sym == SDLK_ESCAPE) {
                done = true;
            }
            start.handle(e);
            retry.handle(e);
            wifi.handle(e);
            back.handle(e);
        }
        int mx, my;
        SDL_GetMouseState(&mx, &my);
        set_color(r, BG);
        SDL_RenderClear(r);
        text(r, 40, 40, "FINDING THE RASPBERRY PI", 3, TEXT);
        if (scanning.load()) {
            text(r, 40, 96, "Looking on LAN, mDNS, every local /24, then Pi Wi-Fi APs...", 2,
                 ACCENT);
        } else if (disc.host) {
            text(r, 40, 96, "Found Pi at " + *disc.host + " via " + disc.via + ".", 2, GOOD);
            if (disc.joined_ap)
                text(r, 40, 132, "Joined the Pi access point '" + *disc.joined_ap + "'.", 2, GOOD);
            if (disc.needs_internet_prompt)
                text(r, 40, 156,
                     "Pi reports no internet. Press \"Pi Wi-Fi setup\" to put it on a network.", 2,
                     WARN);
            text(r, 40, 180,
                 "Now turn on RC, plug RC into Pi, power the drone, wait for link, then start.", 2,
                 MUTED);
        } else {
            text(r, 40, 96,
                 "Pi not found on the LAN and no 'PI_DJI_LINK-*' AP in range. "
                 "Power it on or pass --pi HOST[:PORT].",
                 2, WARN);
        }
        start.draw(r, mx, my);
        retry.draw(r, mx, my);
        wifi.draw(r, mx, my);
        back.draw(r, mx, my);
        auto lines = applog::tail();
        int y = h - 260;
        text(r, 40, y - 26, "LOG TAIL", 2, MUTED);
        for (auto it = lines.size() > 7 ? lines.end() - 7 : lines.begin(); it != lines.end();
             ++it) {
            text(r, 40, y, it->substr(0, 115), 1, MUTED);
            y += 16;
        }
        SDL_RenderPresent(r);
        SDL_Delay(16);
    }
    if (worker.joinable())
        worker.join();
    return result;
}

std::optional<ConnectionSpec> preflight(SDL_Window* win, SDL_Renderer* r, const AppOptions& opt,
                                        const std::optional<std::string>& saved_host) {
    if (opt.sim)
        return ConnectionSpec{"sim"};
    if (!opt.serial.empty())
        return ConnectionSpec{"serial", "", 0, opt.serial};
    if (!opt.pi.empty()) {
        ConnectionSpec s{"pi", opt.pi, netfind::BRIDGE_PORT, ""};
        if (auto pos = s.host.find(':'); pos != std::string::npos) {
            s.port = std::stoi(s.host.substr(pos + 1));
            s.host = s.host.substr(0, pos);
        }
        return s;
    }
    while (true) {
        MenuResult m = menu_screen(win, r);
        if (m == MenuResult::Quit)
            return ConnectionSpec{"quit"};
        if (m == MenuResult::Sim)
            return ConnectionSpec{"sim"};
        if (m == MenuResult::Serial) {
            auto s = serial_screen(win, r, opt.serial);
            if (s && s->mode == "quit")
                return s;
            if (s)
                return s;
            continue;
        }
        if (m == MenuResult::Pi) {
            auto s = discovery_screen(win, r, saved_host);
            if (s && s->mode == "quit")
                return s;
            if (s)
                return s;
        }
    }
}

class FfmpegVideoSink final : public VideoOut {
public:
    explicit FfmpegVideoSink(Client* cli) : cli_(cli) {
        start();
    }
    ~FfmpegVideoSink() override {
        close();
    }

    void on_frame(const Bytes& frame, bool) override {
        if (!ok_.load())
            return;
#ifdef _WIN32
        DWORD written = 0;
        if (!WriteFile(in_write_, frame.data(), static_cast<DWORD>(frame.size()), &written,
                       nullptr))
            ok_.store(false);
#else
        const std::uint8_t* p = frame.data();
        std::size_t left = frame.size();
        while (left > 0) {
            ssize_t n = ::write(in_write_, p, left);
            if (n <= 0) {
                ok_.store(false);
                return;
            }
            p += n;
            left -= static_cast<std::size_t>(n);
        }
#endif
    }

    std::optional<Bytes> take_frame() {
        std::lock_guard<std::mutex> lk(mu_);
        if (latest_.empty())
            return std::nullopt;
        return latest_;
    }

    bool ok() const {
        return ok_.load();
    }

private:
    void start() {
        const std::string ffmpeg_exe = ffmpeg::executable();
#ifndef _WIN32
        std::signal(SIGPIPE, SIG_IGN);
#endif
        const std::vector<std::string> args = {ffmpeg_exe,
                                               "-hide_banner",
                                               "-loglevel",
                                               "error",
                                               "-fflags",
                                               "nobuffer",
                                               "-flags",
                                               "low_delay",
                                               "-flags2",
                                               "+showall",
                                               "-err_detect",
                                               "ignore_err",
                                               "-avioflags",
                                               "direct",
                                               "-fpsprobesize",
                                               "0",
                                               "-f",
                                               "hevc",
                                               "-i",
                                               "-",
                                               "-flush_packets",
                                               "1",
                                               "-fps_mode",
                                               "passthrough",
                                               "-vf",
                                               "scale=640:360",
                                               "-pix_fmt",
                                               "rgb24",
                                               "-f",
                                               "rawvideo",
                                               "-"};
#ifdef _WIN32
        SECURITY_ATTRIBUTES sa{};
        sa.nLength = sizeof(sa);
        sa.bInheritHandle = TRUE;
        HANDLE in_read = nullptr, out_write = nullptr;
        if (!CreatePipe(&in_read, &in_write_, &sa, 0) ||
            !CreatePipe(&out_read_, &out_write, &sa, 0)) {
            ok_.store(false);
            return;
        }
        SetHandleInformation(in_write_, HANDLE_FLAG_INHERIT, 0);
        SetHandleInformation(out_read_, HANDLE_FLAG_INHERIT, 0);
        std::string cmd;
        for (const auto& a : args) {
            if (!cmd.empty())
                cmd += ' ';
            cmd += '"' + a + '"';
        }
        STARTUPINFOA si{};
        si.cb = sizeof(si);
        si.dwFlags = STARTF_USESTDHANDLES;
        si.hStdInput = in_read;
        si.hStdOutput = out_write;
        si.hStdError = GetStdHandle(STD_ERROR_HANDLE);
        std::vector<char> mut(cmd.begin(), cmd.end());
        mut.push_back('\0');
        if (!CreateProcessA(nullptr, mut.data(), nullptr, nullptr, TRUE, CREATE_NO_WINDOW, nullptr,
                            nullptr, &si, &pi_)) {
            CloseHandle(in_read);
            CloseHandle(out_write);
            ok_.store(false);
            applog::info("[video] bundled ffmpeg failed to start - no picture");
            return;
        }
        CloseHandle(in_read);
        CloseHandle(out_write);
#else
        int inpipe[2] = {-1, -1}, outpipe[2] = {-1, -1};
        if (::pipe(inpipe) != 0 || ::pipe(outpipe) != 0) {
            ok_.store(false);
            return;
        }
        pid_ = ::fork();
        if (pid_ == 0) {
            ::dup2(inpipe[0], STDIN_FILENO);
            ::dup2(outpipe[1], STDOUT_FILENO);
            ::close(inpipe[0]);
            ::close(inpipe[1]);
            ::close(outpipe[0]);
            ::close(outpipe[1]);
            std::vector<char*> argv;
            for (const auto& a : args)
                argv.push_back(const_cast<char*>(a.c_str()));
            argv.push_back(nullptr);
            ::execvp(ffmpeg_exe.c_str(), argv.data());
            _exit(127);
        }
        ::close(inpipe[0]);
        ::close(outpipe[1]);
        in_write_ = inpipe[1];
        out_read_ = outpipe[0];
        if (pid_ < 0) {
            ok_.store(false);
            return;
        }
#endif
        ok_.store(true);
        reader_ = std::thread([this] { read_loop(); });
        applog::info("[video] decoder started");
    }

    void read_loop() {
        constexpr std::size_t n = kVideoW * kVideoH * 3;
        Bytes buf(n);
        while (ok_.load()) {
            std::size_t got = 0;
            while (got < n) {
#ifdef _WIN32
                DWORD rd = 0;
                if (!ReadFile(out_read_, buf.data() + got, static_cast<DWORD>(n - got), &rd,
                              nullptr) ||
                    rd == 0) {
                    ok_.store(false);
                    return;
                }
                got += rd;
#else
                ssize_t rd = ::read(out_read_, buf.data() + got, n - got);
                if (rd <= 0) {
                    ok_.store(false);
                    return;
                }
                got += static_cast<std::size_t>(rd);
#endif
            }
            {
                std::lock_guard<std::mutex> lk(mu_);
                latest_ = buf;
            }
            if (cli_)
                cli_->bump_decoded();
        }
    }

    void close() {
        ok_.store(false);
#ifdef _WIN32
        if (in_write_)
            CloseHandle(in_write_);
        if (out_read_)
            CloseHandle(out_read_);
        if (pi_.hProcess)
            TerminateProcess(pi_.hProcess, 0);
        if (reader_.joinable())
            reader_.join();
        if (pi_.hProcess)
            CloseHandle(pi_.hProcess);
        if (pi_.hThread)
            CloseHandle(pi_.hThread);
#else
        if (in_write_ >= 0)
            ::close(in_write_);
        if (out_read_ >= 0)
            ::close(out_read_);
        if (reader_.joinable())
            reader_.join();
        if (pid_ > 0) {
            ::kill(pid_, SIGTERM);
            int st = 0;
            ::waitpid(pid_, &st, 0);
        }
#endif
    }

    Client* cli_ = nullptr;
    std::atomic<bool> ok_{false};
    std::thread reader_;
    std::mutex mu_;
    Bytes latest_;
#ifdef _WIN32
    HANDLE in_write_ = nullptr;
    HANDLE out_read_ = nullptr;
    PROCESS_INFORMATION pi_{};
#else
    int in_write_ = -1;
    int out_read_ = -1;
    pid_t pid_ = -1;
#endif
};

// Hand the command to the client's worker thread instead of running it here: callers are
// on the render thread, and a blocking socket write would freeze the window mid-flight.
// Every captured object is a Client/Settings member that outlives the queued item — the
// queue is drained in Client::close() before either is torn down. Kept at namespace scope
// so both the settings panel and the flight_screen hotkeys can reach it.
void call(Client& cli, std::function<void()> fn, const std::string& msg) {
    cli.post(std::move(fn), msg);
}

struct Settings {
    bool open = false;
    int dragging_slider = -1;
    int max_alt = 120;
    int max_dist = 500;
    int rth_alt = 30;
    int ev = 0;
    int iso_i = 0;
    int shutter_i = 0;
    int mode_i = 1;
    std::vector<int> isos{0, 100, 200, 400, 800, 1600, 3200};
    std::vector<int> shutters{0, 1000, 500, 250, 125, 60, 30, 15, 8, 4};

    static SDL_Rect panel(int sw, int sh) {
        return {std::max(24, (sw - 700) / 2), std::max(20, (sh - 560) / 2), std::min(700, sw - 48),
                std::min(560, sh - 40)};
    }

    static SDL_Rect slider_track(const SDL_Rect& p, int row) {
        return {p.x + 250, p.y + 78 + row * 48 + 15, std::max(100, p.w - 385), 8};
    }

    void set_slider(int row, int mouse_x, const SDL_Rect& p) {
        const SDL_Rect track = slider_track(p, row);
        const int lo = row == 0 ? 15 : (row == 1 ? 15 : 20);
        const int hi = row == 1 ? 5000 : 500;
        const int step = row == 1 ? 50 : 5;
        const double t = std::clamp((mouse_x - track.x) / static_cast<double>(track.w), 0.0, 1.0);
        const double raw = lo + t * (hi - lo);
        const int value = std::clamp(static_cast<int>(std::lround(raw / step)) * step, lo, hi);
        if (row == 0)
            max_alt = value;
        else if (row == 1)
            max_dist = value;
        else
            rth_alt = value;
    }

    void commit_slider(int row, Client& cli) {
        if (row == 0) {
            call(
                cli, [&cli, v = max_alt] { cli.drone().set_max_altitude(v); },
                "max alt " + std::to_string(max_alt) + " m");
        } else if (row == 1) {
            call(
                cli, [&cli, v = max_dist] { cli.drone().set_max_distance(v); },
                "max dist " + std::to_string(max_dist) + " m");
        } else if (row == 2) {
            call(
                cli, [&cli, v = rth_alt] { cli.drone().set_rth_altitude(v); },
                "RTH alt " + std::to_string(rth_alt) + " m");
        }
    }

    void draw(SDL_Renderer* r, Client& cli, int sw, int sh, int mx, int my) {
        SDL_Rect p = panel(sw, sh);
        fill_round(r, p, 14, PANEL);
        outline_round(r, p, 14, PANEL_HI);
        text(r, p.x + 24, p.y + 24, "Flight settings", 3, ACCENT);
        text(r, p.x + 24, p.y + p.h - 28, "Esc closes.", 1, MUTED);

        std::vector<Button> b;
        int y = p.y + 78;
        auto slider_row = [&](const std::string& label, int value, int lo, int hi, int row) {
            text(r, p.x + 26, y + 12, label, 2, TEXT);
            SDL_Rect track = slider_track(p, row);
            fill_round(r, track, 4, Color{50, 55, 68, 255});
            const double t = std::clamp((value - lo) / static_cast<double>(hi - lo), 0.0, 1.0);
            const int knob_x = track.x + static_cast<int>(std::lround(t * track.w));
            if (knob_x > track.x)
                fill_round(r, SDL_Rect{track.x, track.y, knob_x - track.x, track.h}, 4, ACCENT);
            const bool hot = dragging_slider == row ||
                             inside(SDL_Rect{track.x - 8, track.y - 10, track.w + 16, 28}, mx, my);
            fill_round(r, SDL_Rect{knob_x - 8, track.y - 4, 16, 16}, 8, hot ? ACCENT_HI : ACCENT);
            text(r, p.x + p.w - 105, y + 12, std::to_string(value) + " M", 2, ACCENT_HI);
            y += 48;
        };
        auto row = [&](const std::string& label, const std::string& val, auto minus, auto plus) {
            text(r, p.x + 26, y + 12, label, 2, TEXT);
            text(r, p.x + 395, y + 12, val, 2, ACCENT_HI);
            b.push_back({{p.x + p.w - 150, y, 46, 38}, "-", false, true, minus});
            b.push_back({{p.x + p.w - 92, y, 46, 38}, "+", false, true, plus});
            y += 48;
        };
        slider_row("MAX ALTITUDE", max_alt, 15, 500, 0);
        slider_row("MAX DISTANCE", max_dist, 15, 5000, 1);
        slider_row("RTH ALTITUDE", rth_alt, 20, 500, 2);
        row(
            "EV", (ev > 0 ? "+" : "") + std::to_string(ev),
            [&] {
                ev = std::max(-3, ev - 1);
                call(cli, [&cli, v = ev] { cli.drone().set_ev(v); }, "EV " + std::to_string(ev));
            },
            [&] {
                ev = std::min(3, ev + 1);
                call(cli, [&cli, v = ev] { cli.drone().set_ev(v); }, "EV " + std::to_string(ev));
            });
        row(
            "ISO", isos[iso_i] == 0 ? "AUTO" : std::to_string(isos[iso_i]),
            [&] {
                iso_i = (iso_i + static_cast<int>(isos.size()) - 1) % static_cast<int>(isos.size());
                call(
                    cli,
                    [&cli, v = isos[iso_i]] {
                        if (v == 0)
                            cli.drone().set_iso_auto();
                        else
                            cli.drone().set_iso(v);
                    },
                    isos[iso_i] == 0 ? "ISO auto" : "ISO " + std::to_string(isos[iso_i]));
            },
            [&] {
                iso_i = (iso_i + 1) % static_cast<int>(isos.size());
                call(
                    cli,
                    [&cli, v = isos[iso_i]] {
                        if (v == 0)
                            cli.drone().set_iso_auto();
                        else
                            cli.drone().set_iso(v);
                    },
                    isos[iso_i] == 0 ? "ISO auto" : "ISO " + std::to_string(isos[iso_i]));
            });
        row(
            "SHUTTER",
            shutters[shutter_i] == 0 ? "AUTO" : "1/" + std::to_string(shutters[shutter_i]),
            [&] {
                shutter_i = (shutter_i + static_cast<int>(shutters.size()) - 1) %
                            static_cast<int>(shutters.size());
                call(
                    cli,
                    [&cli, v = shutters[shutter_i]] {
                        if (v == 0)
                            cli.drone().set_shutter_auto();
                        else
                            cli.drone().set_shutter(v);
                    },
                    shutters[shutter_i] == 0 ? "shutter AUTO"
                                             : "shutter 1/" + std::to_string(shutters[shutter_i]));
            },
            [&] {
                shutter_i = (shutter_i + 1) % static_cast<int>(shutters.size());
                call(
                    cli,
                    [&cli, v = shutters[shutter_i]] {
                        if (v == 0)
                            cli.drone().set_shutter_auto();
                        else
                            cli.drone().set_shutter(v);
                    },
                    shutters[shutter_i] == 0 ? "shutter AUTO"
                                             : "shutter 1/" + std::to_string(shutters[shutter_i]));
            });

        b.push_back({{p.x + 26, y + 8, 190, 42}, "Flight normal", false, true, [&] {
                         call(cli, [&] { cli.drone().set_flight_mode("normal"); }, "mode normal");
                     }});
        b.push_back({{p.x + 226, y + 8, 190, 42}, "Flight cinema", false, true, [&] {
                         call(cli, [&] { cli.drone().set_flight_mode("cinema"); }, "mode cinema");
                     }});
        b.push_back({{p.x + 426, y + 8, 190, 42}, "Flight sport", false, true, [&] {
                         call(cli, [&] { cli.drone().set_flight_mode("sport"); }, "mode sport");
                     }});
        y += 58;
        b.push_back({{p.x + 26, y, 190, 42}, "Recenter gimbal", false, true, [&] {
                         call(cli, [&] { cli.drone().gimbal_recenter(); }, "gimbal recenter");
                     }});
        b.push_back({{p.x + 226, y, 190, 42}, "Set home here", false, true, [&] {
                         call(cli, [&] { cli.drone().set_home_to_current_location(); }, "home set");
                     }});
        b.push_back({{p.x + 426, y, 190, 42}, "Exit to menu", false, true, [&] {
                         cli.return_to_menu.store(true);
                         cli.set_msg("returning to menu");
                     }});
        for (const auto& x : b)
            x.draw(r, mx, my);
    }

    bool handle(const SDL_Event& e, Client& cli, int sw, int sh) {
        if (!open)
            return false;
        if (e.type == SDL_KEYDOWN && e.key.keysym.sym == SDLK_ESCAPE) {
            dragging_slider = -1;
            open = false;
            return true;
        }
        SDL_Rect p = panel(sw, sh);
        if (e.type == SDL_MOUSEMOTION && dragging_slider >= 0) {
            set_slider(dragging_slider, e.motion.x, p);
            return true;
        }
        if (e.type == SDL_MOUSEBUTTONUP && e.button.button == SDL_BUTTON_LEFT &&
            dragging_slider >= 0) {
            const int row = dragging_slider;
            set_slider(row, e.button.x, p);
            dragging_slider = -1;
            commit_slider(row, cli);
            return true;
        }
        if (e.type != SDL_MOUSEBUTTONDOWN || e.button.button != SDL_BUTTON_LEFT)
            return true;
        int mx = e.button.x, my = e.button.y;
        for (int row = 0; row < 3; ++row) {
            const SDL_Rect track = slider_track(p, row);
            if (inside(SDL_Rect{track.x - 8, track.y - 10, track.w + 16, 28}, mx, my)) {
                dragging_slider = row;
                set_slider(row, mx, p);
                return true;
            }
        }
        int y = p.y + 78;
        auto in_minus = [&](int row) {
            SDL_Rect rc{p.x + p.w - 150, p.y + 78 + row * 48, 46, 38};
            return inside(rc, mx, my);
        };
        auto in_plus = [&](int row) {
            SDL_Rect rc{p.x + p.w - 92, p.y + 78 + row * 48, 46, 38};
            return inside(rc, mx, my);
        };
        auto row_action = [&](int row, auto minus, auto plus) {
            if (in_minus(row)) {
                minus();
                return true;
            }
            if (in_plus(row)) {
                plus();
                return true;
            }
            return false;
        };
        if (row_action(
                3,
                [&] {
                    ev = std::max(-3, ev - 1);
                    call(
                        cli, [&cli, v = ev] { cli.drone().set_ev(v); }, "EV " + std::to_string(ev));
                },
                [&] {
                    ev = std::min(3, ev + 1);
                    call(
                        cli, [&cli, v = ev] { cli.drone().set_ev(v); }, "EV " + std::to_string(ev));
                }))
            return true;
        if (row_action(
                4,
                [&] {
                    iso_i =
                        (iso_i + static_cast<int>(isos.size()) - 1) % static_cast<int>(isos.size());
                    call(
                        cli,
                        [&cli, v = isos[iso_i]] {
                            if (v == 0)
                                cli.drone().set_iso_auto();
                            else
                                cli.drone().set_iso(v);
                        },
                        isos[iso_i] == 0 ? "ISO auto" : "ISO " + std::to_string(isos[iso_i]));
                },
                [&] {
                    iso_i = (iso_i + 1) % static_cast<int>(isos.size());
                    call(
                        cli,
                        [&cli, v = isos[iso_i]] {
                            if (v == 0)
                                cli.drone().set_iso_auto();
                            else
                                cli.drone().set_iso(v);
                        },
                        isos[iso_i] == 0 ? "ISO auto" : "ISO " + std::to_string(isos[iso_i]));
                }))
            return true;
        if (row_action(
                5,
                [&] {
                    shutter_i = (shutter_i + static_cast<int>(shutters.size()) - 1) %
                                static_cast<int>(shutters.size());
                    call(
                        cli,
                        [&cli, v = shutters[shutter_i]] {
                            if (v == 0)
                                cli.drone().set_shutter_auto();
                            else
                                cli.drone().set_shutter(v);
                        },
                        shutters[shutter_i] == 0
                            ? "shutter AUTO"
                            : "shutter 1/" + std::to_string(shutters[shutter_i]));
                },
                [&] {
                    shutter_i = (shutter_i + 1) % static_cast<int>(shutters.size());
                    call(
                        cli,
                        [&cli, v = shutters[shutter_i]] {
                            if (v == 0)
                                cli.drone().set_shutter_auto();
                            else
                                cli.drone().set_shutter(v);
                        },
                        shutters[shutter_i] == 0
                            ? "shutter AUTO"
                            : "shutter 1/" + std::to_string(shutters[shutter_i]));
                }))
            return true;
        y += 6 * 48 + 8;
        struct Click {
            SDL_Rect r;
            std::function<void()> fn;
        };
        std::vector<Click> clicks = {
            {{p.x + 26, y, 190, 42},
             [&] { call(cli, [&cli] { cli.drone().set_flight_mode("normal"); }, "mode normal"); }},
            {{p.x + 226, y, 190, 42},
             [&] { call(cli, [&cli] { cli.drone().set_flight_mode("cinema"); }, "mode cinema"); }},
            {{p.x + 426, y, 190, 42},
             [&] { call(cli, [&cli] { cli.drone().set_flight_mode("sport"); }, "mode sport"); }},
            {{p.x + 26, y + 58, 190, 42},
             [&] { call(cli, [&cli] { cli.drone().gimbal_recenter(); }, "gimbal recenter"); }},
            {{p.x + 226, y + 58, 190, 42},
             [&] {
                 call(cli, [&cli] { cli.drone().set_home_to_current_location(); }, "home set");
             }},
            {{p.x + 426, y + 58, 190, 42}, [&] { cli.return_to_menu.store(true); }},
        };
        for (const auto& c : clicks) {
            if (inside(c.r, mx, my)) {
                c.fn();
                return true;
            }
        }
        return true;
    }
};

// A rounded status chip with centred text (beta _hud_chip).
void hud_chip(SDL_Renderer* r, const SDL_Rect& rc, const std::string& s, Color fg, Color border,
              Color fillc) {
    fill_round(r, rc, 7, fillc);
    outline_round(r, rc, 7, border);
    text_center(r, rc, s, 1, fg);
}

// A small square stick pad with a dot at (x,y) in [-1,1] and a caption (beta _hud_pad).
void stick_pad(SDL_Renderer* r, int cx, int cy, int half, double x, double y,
               const std::string& label) {
    SDL_Rect rc{cx - half, cy - half, half * 2, half * 2};
    fill_round(r, rc, 6, Color{10, 12, 16, 150});
    outline_round(r, rc, 6, Color{70, 76, 92, 255});
    set_color(r, Color{46, 50, 62, 255});
    SDL_RenderDrawLine(r, rc.x + rc.w / 2, rc.y + 4, rc.x + rc.w / 2, rc.y + rc.h - 4);
    SDL_RenderDrawLine(r, rc.x + 4, rc.y + rc.h / 2, rc.x + rc.w - 4, rc.y + rc.h / 2);
    const double dx = std::max(-1.0, std::min(1.0, x)), dy = std::max(-1.0, std::min(1.0, y));
    const int px = static_cast<int>(rc.x + rc.w / 2 + dx * (half - 6));
    const int py = static_cast<int>(rc.y + rc.h / 2 - dy * (half - 6));
    fill_round(r, SDL_Rect{px - 5, py - 5, 10, 10}, 5, ACCENT_HI);
    text_center(r, SDL_Rect{rc.x, rc.y + rc.h + 2, rc.w, 14}, label, 1, MUTED);
}

// Faithful port of the beta's _draw_flight_hud (pc_client.py): top-left status card
// (title + mode chip, battery bar, ARMED/CTRL/FC chips, altitude/fly-time/mode grid,
// home + limits + hint), a top-right REC badge and twin bottom-right stick pads. The
// satellite count / GPS level cell is shown like the beta; only the GPS *position*
// readout (lat/lon) from the beta is intentionally left out for now.
void draw_hud(SDL_Renderer* r, Client& cli, int sw, int sh) {
    if (!cli.show_hud.load())
        return;
    const auto& st = cli.tele().state();

    const int X = 16, Y = 16, W = 300, pad = 14, card_h = 246;
    fill_round(r, SDL_Rect{X, Y, W, card_h}, 12, Color{18, 20, 26, 205});
    outline_round(r, SDL_Rect{X, Y, W, card_h}, 12, Color{48, 53, 66, 255});
    const int lx = X + pad, rx = X + W - pad;
    int y = Y + 16;

    // title + mode chip
    text(r, lx, y - 2, "DJI Mavic Mini 1", 2, TEXT);
    {
        std::string m = cli.mode();
        std::transform(m.begin(), m.end(), m.begin(),
                       [](unsigned char ch) { return static_cast<char>(std::toupper(ch)); });
        const std::string mode_txt = m + " - " + (cli.live() ? "LIVE" : "DRY");
        const int cw = text_w(mode_txt, 1) + 16;
        hud_chip(r, SDL_Rect{rx - cw, y, cw, 20}, mode_txt, ACCENT_HI, Color{60, 90, 130, 255},
                 Color{30, 40, 56, 255});
    }
    y += 30;

    // battery bar
    const int pct = st.battery_pct.value_or(0);
    const Color bcol = pct > 50 ? GOOD : (pct > 20 ? WARN : BAD);
    SDL_Rect bar{lx, y, W - 2 * pad, 20};
    fill_round(r, bar, 6, Color{36, 40, 52, 255});
    const int fillw = (W - 2 * pad) * std::max(0, std::min(100, pct)) / 100;
    if (fillw > 8)
        fill_round(r, SDL_Rect{bar.x, bar.y, fillw, bar.h}, 6, bcol);
    text(r, lx + 8, y + 4, std::to_string(pct) + "%", 1, pct > 20 ? Color{12, 16, 20, 255} : TEXT);
    if (st.battery_mv) {
        char v[16];
        std::snprintf(v, sizeof(v), "%.1fV", *st.battery_mv / 1000.0);
        text(r, rx - 8 - text_w(v, 1), y + 4, v, 1, TEXT);
    }
    y += 30;

    // status chips: ARMED / CONTROL / FC-owner
    const std::string owner = st.ctrl_device ? sdk_ctrl_device_name(*st.ctrl_device) : "?";
    struct ChipDef {
        std::string s;
        bool on;
    };
    const std::vector<ChipDef> chips = {
        {cli.armed.load() ? "ARMED" : "DISARMED", cli.armed.load()},
        {cli.control.load() ? "CTRL ON" : "CTRL OFF", cli.control.load()},
        {"FC:" + owner, st.ctrl_device.value_or(-1) == 1},
    };
    int cxp = lx;
    for (const auto& ch : chips) {
        const int w = text_w(ch.s, 1) + 16;
        const Color fg = ch.on ? GOOD : MUTED;
        hud_chip(r, SDL_Rect{cxp, y, w, 22}, ch.s, fg,
                 Color{static_cast<std::uint8_t>(fg.r / 3), static_cast<std::uint8_t>(fg.g / 3),
                       static_cast<std::uint8_t>(fg.b / 3), 255},
                 ch.on ? Color{24, 34, 26, 255} : Color{30, 32, 40, 255});
        cxp += w + 6;
    }
    y += 32;

    // telemetry grid — two columns of label/value
    const int c0 = lx, c1 = lx + (W - 2 * pad) / 2 + 6;
    auto cell = [&](int col_x, const char* label, const std::string& value) {
        text(r, col_x, y + 4, label, 1, MUTED);
        text(r, col_x, y + 18, value, 2, TEXT);
    };
    std::string alt = "-";
    if (st.altitude_m) {
        char b[24];
        std::snprintf(b, sizeof(b), "%.1f m", *st.altitude_m);
        alt = b;
    }
    std::string ft = "-";
    if (st.flight_time_s) {
        char b[16];
        std::snprintf(b, sizeof(b), "%d:%02d", *st.flight_time_s / 60, *st.flight_time_s % 60);
        ft = b;
    }
    cell(c0, "ALTITUDE", alt);
    cell(c1, "FLY TIME", ft);
    y += 40;
    // SATS / GPS — parity with the beta's _draw_flight_hud: satellite count (@0x24) and
    // GPS level (bits 18..21 of the u32 @0x20, 0..5) share one row with MODE, exactly
    // like pc_client.py does (`_hud_cell(c0, "SATS · GPS", ...)` next to MODE at c1).
    // "·" is not ASCII: the bitmap fallback can't draw it and the stb path treats each
    // UTF-8 byte separately, so the HUD uses "/" instead.
    const std::string sats_gps =
        (st.satellites ? std::to_string(*st.satellites) : std::string("-")) + " / " +
        (st.gps_level ? std::to_string(*st.gps_level) : std::string("-"));
    cell(c0, "SATS / GPS", sats_gps);
    cell(c1, "MODE", st.flight_mode_name.value_or("-"));
    y += 42;

    // home + limits + hint
    auto lim = [](const std::optional<double>& v) {
        return v ? (std::to_string(static_cast<int>(*v)) + "m") : std::string("-");
    };
    text(r, lx, y, std::string("home ") + (st.home_recorded.value_or(false) ? "set" : "not set"), 1,
         MUTED);
    text(r, lx, y + 18,
         "alt<=" + lim(st.max_height_m) + "  dist<=" + lim(st.max_distance_m) + "  RTH " +
             lim(st.rth_altitude_m),
         1, MUTED);
    text(r, lx, y + 36, "F1 help    Esc settings    F3 hide", 1, Color{110, 116, 130, 255});

    // motor-start failure banner (only when relevant)
    if (st.motor_fail_code && *st.motor_fail_code != 0) {
        SDL_Rect fb{X, Y + card_h + 6, W, 26};
        fill_round(r, fb, 6, Color{80, 20, 24, 210});
        text(r, fb.x + 10, fb.y + 6, "WON'T START: " + st.motor_fail_reason.value_or(""), 1, BAD);
    }

    // top-right REC badge
    if (st.is_recording.value_or(false)) {
        const std::string rt = "REC " + std::to_string(st.record_time_s.value_or(0)) + "s";
        const int w = text_w(rt, 2) + 44;
        SDL_Rect rb{sw - 16 - w, 16, w, 30};
        fill_round(r, rb, 8, Color{20, 12, 14, 200});
        outline_round(r, rb, 8, Color{120, 40, 44, 255});
        fill_round(r, SDL_Rect{rb.x + 12, rb.y + rb.h / 2 - 6, 12, 12}, 6, BAD);
        text(r, rb.x + 32, rb.y + 7, rt, 2, TEXT);
    }

    // bottom-right twin stick pads
    const Sticks a = cli.axes();
    // Yaw from the mouse lives in the pending accumulator until the 20 Hz sender loop
    // folds it in; blend it in for display so the pad reacts to mouse movement too
    // (same scale the sender applies: Client::kMouseYawSens).
    const double yaw = std::clamp(a.yaw + cli.peek_mouse_dx() * Client::kMouseYawSens, -1.0, 1.0);
    const int half = 42, gap = 24;
    const int base_y = sh - half - 34;
    const int rpad_cx = sw - 16 - half;
    const int lpad_cx = rpad_cx - half * 2 - gap;
    stick_pad(r, lpad_cx, base_y, half, yaw, a.throttle, "yaw / thr");
    stick_pad(r, rpad_cx, base_y, half, a.roll, a.pitch, "roll / pitch");
}

// Faithful port of the beta's _draw_help / _HELP_SECTIONS (pc_client.py): a dimmed
// backdrop + a card titled "Controls & help", the same four grouped sections laid out
// in two columns (FLIGHT/MOVE left, CAMERA/VIEW&SYSTEM right), key + description rows.
void draw_help(SDL_Renderer* r, int sw, int sh) {
    struct Row {
        const char* key;
        const char* desc;
    };
    struct Section {
        const char* title;
        std::vector<Row> rows;
    };
    static const std::vector<Section> sections = {
        {"FLIGHT - do these in order",
         {{"Enter", "ARM / disarm motors - always first"},
          {"T", "take off (control auto-enables once stable)"},
          {"C", "control on/off - only AFTER takeoff"},
          {"L", "land (auto-releases control back to RC)"},
          {"H", "Return-to-Home - emergency recall"}}},
        {"MOVE - hold while flying",
         {{"W / S", "pitch forward / back"},
          {"A / D", "roll left / right"},
          {"Space / Shift", "throttle up / down"},
          {"Q / E", "yaw left / right"},
          {"Mouse", "yaw (left-right) + gimbal tilt (up-down)"}}},
        {"CAMERA",
         {{"P", "take a photo"},
          {"R", "start / stop recording"},
          {"[ ] or Up/Down", "gimbal tilt"},
          {"N", "recenter gimbal"}}},
        {"VIEW & SYSTEM",
         {{"Esc", "flight settings panel"},
          {"Tab", "console (type any command)"},
          {"F1", "this help (Esc/F1 to close)"},
          {"F3", "hide / show the HUD"},
          {"F11", "fullscreen toggle"},
          {"V", "ground-station authority toggle"},
          {"U", "no-GPS takeoff unlock"},
          {"K", "request a video keyframe"},
          {"G", "cycle stick flag (debug)"}}},
    };
    const int pw = std::min(940, sw - 40);
    const int ph = std::min(600, sh - 40);
    const int px = (sw - pw) / 2, py = (sh - ph) / 2;
    fill(r, SDL_Rect{0, 0, sw, sh}, Color{0, 0, 0, 150}); // dim the world
    fill_round(r, SDL_Rect{px, py, pw, ph}, 14, PANEL);
    outline_round(r, SDL_Rect{px, py, pw, ph}, 14, PANEL_HI);
    text(r, px + 26, py + 18, "Controls & help", 3, ACCENT);
    set_color(r, PANEL_HI);
    SDL_RenderDrawLine(r, px + 26, py + 60, px + pw - 26, py + 60);

    const int col_w = (pw - 52) / 2;
    int col_x[2] = {px + 26, px + 26 + col_w};
    int col_y[2] = {py + 76, py + 76};
    const int layout[4] = {0, 0, 1, 1}; // FLIGHT+MOVE left, CAMERA+VIEW&SYSTEM right
    for (std::size_t si = 0; si < sections.size(); ++si) {
        const int c = layout[si];
        int x = col_x[c], y = col_y[c];
        text(r, x, y, sections[si].title, 1, ACCENT_HI);
        y += 24;
        for (const auto& row : sections[si].rows) {
            text(r, x + 6, y, row.key, 1, TEXT);
            text(r, x + 150, y, row.desc, 1, Color{188, 196, 208, 255});
            y += 22;
        }
        y += 14;
        col_y[c] = y;
    }
    text_center(r, SDL_Rect{px, py + ph - 30, pw, 22}, "Esc or F1 - close", 1, MUTED);
}

void draw_console(SDL_Renderer* r, int sw, int sh, const std::string& buf) {
    SDL_Rect p{18, sh - 74, sw - 36, 56};
    fill_round(r, p, 10, Color{8, 10, 14, 235});
    outline_round(r, p, 10, ACCENT);
    text(r, p.x + 14, p.y + 18, "> " + buf + "_", 2, TEXT);
}

int flight_screen(SDL_Window* win, SDL_Renderer* r, const ConnectionSpec& spec,
                  const AppOptions& opt) {
    std::unique_ptr<Transport> t;
    try {
        t = make_transport(spec);
    } catch (const std::exception& e) {
        applog::info(std::string("[connect] ") + e.what());
        return 1;
    }
    const bool live = spec.mode != "sim" && !opt.dry;
    Client cli(std::move(t), spec.mode, live);
    cli.drone().encrypt_config = false;
    std::unique_ptr<FfmpegVideoSink> video;
    if (spec.mode == "pi" && !opt.no_video) {
        video = std::make_unique<FfmpegVideoSink>(&cli);
        cli.set_video_out(video.get());
    }
    cli.start();
    if (spec.mode == "pi" && !opt.no_video)
        cli.start_video();

    bool running = true, help = false, console = false, fullscreen = !opt.windowed;
    std::string cbuf;
    Settings settings;
    SDL_Texture* tex =
        SDL_CreateTexture(r, SDL_PIXELFORMAT_RGB24, SDL_TEXTUREACCESS_STREAMING, kVideoW, kVideoH);
    std::set<std::string> held;
    double gimbal_pitch = 0.0;
    auto gimbal_last = std::chrono::steady_clock::now();
    auto set_grab = [&](bool on) {
        SDL_SetWindowGrab(win, on ? SDL_TRUE : SDL_FALSE);
        SDL_SetRelativeMouseMode(on ? SDL_TRUE : SDL_FALSE);
    };
    set_grab(true);
    SDL_StartTextInput();
    while (running && !cli.return_to_menu.load()) {
        SDL_Event e;
        int sw, sh;
        SDL_GetWindowSize(win, &sw, &sh);
        while (SDL_PollEvent(&e)) {
            if (e.type == SDL_QUIT) {
                running = false;
            } else if (help) {
                if (e.type == SDL_KEYDOWN &&
                    (e.key.keysym.sym == SDLK_ESCAPE || e.key.keysym.sym == SDLK_F1)) {
                    help = false;
                    set_grab(!console && !settings.open);
                }
            } else if (settings.open) {
                settings.handle(e, cli, sw, sh);
                if (!settings.open)
                    set_grab(!console);
            } else if (console) {
                if (e.type == SDL_TEXTINPUT) {
                    cbuf += e.text.text;
                } else if (e.type == SDL_KEYDOWN) {
                    if (e.key.keysym.sym == SDLK_RETURN) {
                        run_console_cmd(cli, cbuf);
                        std::string cmd = cbuf.substr(0, cbuf.find(' '));
                        cbuf.clear();
                        if (cmd == "quit" || cmd == "exit")
                            running = false;
                    } else if (e.key.keysym.sym == SDLK_ESCAPE) {
                        console = false;
                        set_grab(true);
                    } else if (e.key.keysym.sym == SDLK_BACKSPACE) {
                        if (!cbuf.empty())
                            cbuf.pop_back();
                    }
                }
            } else if (e.type == SDL_MOUSEMOTION && SDL_GetRelativeMouseMode()) {
                cli.add_mouse_dx(e.motion.xrel);
                // Mouse Y only updates the absolute pitch target; the frame loop streams it
                // at 10 Hz. Sending per motion event would flood the link with hundreds of
                // frames a second and stall the window (pc_client.py does the same).
                gimbal_pitch = std::max(
                    -90.0, std::min(30.0, gimbal_pitch - e.motion.yrel * kMouseGimbalSens));
            } else if (e.type == SDL_KEYDOWN && !e.key.repeat) {
                const SDL_Keycode k = e.key.keysym.sym;
                if (k == SDLK_ESCAPE) {
                    settings.open = true;
                    set_grab(false);
                } else if (k == SDLK_F1) {
                    help = true;
                    set_grab(false);
                } else if (k == SDLK_F3) {
                    cli.show_hud.store(!cli.show_hud.load());
                    cli.set_msg(cli.show_hud.load() ? "HUD shown" : "HUD hidden - F3 to show");
                } else if (k == SDLK_F11) {
                    fullscreen = !fullscreen;
                    SDL_SetWindowFullscreen(win, fullscreen ? SDL_WINDOW_FULLSCREEN_DESKTOP : 0);
                } else if (k == SDLK_TAB) {
                    console = true;
                    cbuf.clear();
                    set_grab(false);
                } else if (k == SDLK_RETURN) {
                    cli.armed.store(!cli.armed.load());
                    cli.set_msg(cli.armed.load() ? "ARMED=1" : "ARMED=0");
                } else if (k == SDLK_t && cli.flight_ok()) {
                    cli.note_takeoff();
                    call(cli, [&cli] { cli.drone().takeoff(); }, "takeoff");
                } else if (k == SDLK_l) {
                    // Land must also drop virtual-stick control: sender_loop keeps pushing
                    // stick frames at 20 Hz while control is on, and a stream of velocity
                    // setpoints overrides AUTO_LANDING the moment the FC accepts it — the
                    // drone acknowledges (blinks) and stays put. Mirrors the `land` console
                    // command, which already did this.
                    cli.cancel_auto_c();
                    // Clear `control` before queueing so sender_loop stops emitting stick
                    // frames immediately; otherwise it could slip a setpoint in between
                    // land and enable_virtual_stick(false) and cancel the landing.
                    const bool had_control = cli.control.load();
                    if (had_control) {
                        cli.control.store(false);
                        cli.gs.store(false);
                    }
                    call(
                        cli,
                        [&cli, had_control] {
                            cli.drone().land();
                            if (had_control)
                                cli.drone().enable_virtual_stick(false);
                        },
                        had_control ? "land (control auto-OFF, returned to RC)" : "land");
                } else if (k == SDLK_h) {
                    call(cli, [&cli] { cli.drone().return_to_home(); }, "RTH (emergency)");
                } else if (k == SDLK_c) {
                    bool want = !cli.control.load();
                    if (want && !cli.airborne()) {
                        cli.set_msg("control on blocked: take off first");
                    } else {
                        cli.control.store(want);
                        call(
                            cli,
                            [&cli, want] {
                                if (want)
                                    cli.drone().request_control();
                                else
                                    cli.drone().release_control();
                            },
                            std::string("control=") + (want ? "1" : "0"));
                    }
                } else if (k == SDLK_v) {
                    bool on = !cli.gs.load();
                    cli.gs.store(on);
                    call(
                        cli, [&cli, on] { cli.drone().set_ground_station_mode(on); },
                        std::string("ground_station=") + (on ? "1" : "0"));
                } else if (k == SDLK_n) {
                    call(cli, [&cli] { cli.drone().gimbal_recenter(); }, "gimbal recenter");
                } else if (k == SDLK_p) {
                    call(cli, [&cli] { cli.drone().take_photo(); }, "photo");
                } else if (k == SDLK_r) {
                    bool rec = !cli.recording.load();
                    cli.recording.store(rec);
                    call(
                        cli,
                        [&cli, rec] {
                            if (rec)
                                cli.drone().start_record();
                            else
                                cli.drone().stop_record();
                        },
                        rec ? "rec start" : "rec stop");
                } else if (k == SDLK_k) {
                    call(cli, [&cli] { cli.drone().request_i_frame(); }, "keyframe requested");
                } else if (k == SDLK_u) {
                    call(
                        cli, [&cli] { cli.drone().unlock_no_gps(true); },
                        "no-GPS takeoff unlock sent");
                } else if (k == SDLK_m) {
                    cli.stick_mobilerc.store(!cli.stick_mobilerc.load());
                    cli.set_msg(cli.stick_mobilerc.load() ? "stick mobile-RC" : "stick flyc");
                } else if (k == SDLK_g) {
                    static const std::vector<std::uint8_t> flags{0x4A, 0x48, 0x0A, 0x08};
                    auto it = std::find(flags.begin(), flags.end(), cli.stick_flag);
                    std::size_t idx =
                        it == flags.end() ? 0 : (it - flags.begin() + 1) % flags.size();
                    cli.stick_flag = flags[idx];
                    std::ostringstream os;
                    os << "stick flag 0x" << std::hex << static_cast<int>(cli.stick_flag);
                    cli.set_msg(os.str());
                }
            }
        }

        const Uint8* keys = SDL_GetKeyboardState(nullptr);
        held.clear();
        if (keys[SDL_SCANCODE_W])
            held.insert("w");
        if (keys[SDL_SCANCODE_A])
            held.insert("a");
        if (keys[SDL_SCANCODE_S])
            held.insert("s");
        if (keys[SDL_SCANCODE_D])
            held.insert("d");
        if (keys[SDL_SCANCODE_Q])
            held.insert("q");
        if (keys[SDL_SCANCODE_E])
            held.insert("e");
        if (keys[SDL_SCANCODE_SPACE])
            held.insert("space");
        if (keys[SDL_SCANCODE_LSHIFT] || keys[SDL_SCANCODE_RSHIFT])
            held.insert("shift");
        // Keyboard axes only. Mouse yaw is applied in the sender loop, which drains the
        // accumulator — clearing it here would throw away the frames that fall between
        // two 20 Hz sends (see Client::add_mouse_dx).
        cli.set_axes(keys_to_sticks(held));
        if (keys[SDL_SCANCODE_RIGHTBRACKET] || keys[SDL_SCANCODE_UP])
            gimbal_pitch = std::min(30.0, gimbal_pitch + 1.5);
        if (keys[SDL_SCANCODE_LEFTBRACKET] || keys[SDL_SCANCODE_DOWN])
            gimbal_pitch = std::max(-90.0, gimbal_pitch - 1.5);
        // One absolute-target frame per 100 ms, matching pc_client.py's gimbal_last gate.
        // duration 0.12 s slightly overlaps the next send so motion stays continuous.
        {
            const auto now = std::chrono::steady_clock::now();
            if (std::chrono::duration<double>(now - gimbal_last).count() > 0.1) {
                gimbal_last = now;
                cli.post([&cli, v = gimbal_pitch] { cli.drone().gimbal_angle(v, 0.0, 0.0, 0.12); });
            }
        }

        set_color(r, Color{9, 11, 16, 255});
        SDL_RenderClear(r);
        if (video && tex) {
            if (auto f = video->take_frame())
                SDL_UpdateTexture(tex, nullptr, f->data(), kVideoW * 3);
            double k =
                std::min(sw / static_cast<double>(kVideoW), sh / static_cast<double>(kVideoH));
            SDL_Rect dst{(sw - static_cast<int>(kVideoW * k)) / 2,
                         (sh - static_cast<int>(kVideoH * k)) / 2, static_cast<int>(kVideoW * k),
                         static_cast<int>(kVideoH * k)};
            SDL_RenderCopy(r, tex, nullptr, &dst);
        } else {
            text(r, sw / 2 - 190, sh / 2 - 16,
                 spec.mode == "pi" ? "VIDEO DISABLED OR FFMPEG MISSING" : "NO VIDEO IN THIS MODE",
                 2, MUTED);
        }
        draw_hud(r, cli, sw, sh);
        if (settings.open) {
            int mx = 0, my = 0;
            SDL_GetMouseState(&mx, &my);
            settings.draw(r, cli, sw, sh, mx, my);
        }
        if (help)
            draw_help(r, sw, sh);
        if (console)
            draw_console(r, sw, sh, cbuf);
        SDL_RenderPresent(r);
        SDL_Delay(16);
    }
    SDL_StopTextInput();
    set_grab(false);
    if (tex)
        SDL_DestroyTexture(tex);
    cli.close();
    return running ? 0 : 1;
}

} // namespace

int run_app(const AppOptions& opt) {
    // We link without SDL2main and define SDL_MAIN_HANDLED, so SDL's own entry-point
    // shim never runs — announce that main() is ready before touching any SDL API.
    SDL_SetMainReady();
    SDL_SetHint(SDL_HINT_APP_NAME, "DJI Link");
    if (SDL_Init(SDL_INIT_VIDEO | SDL_INIT_EVENTS | SDL_INIT_TIMER) != 0) {
        std::fprintf(stderr, "SDL_Init failed: %s\n", SDL_GetError());
        return 2;
    }
    Uint32 flags = SDL_WINDOW_RESIZABLE;
    if (!opt.windowed)
        flags |= SDL_WINDOW_FULLSCREEN_DESKTOP;
    SDL_Window* win = SDL_CreateWindow("DJI Mavic Mini 1 - PC control", SDL_WINDOWPOS_CENTERED,
                                       SDL_WINDOWPOS_CENTERED, kWinW, kWinH, flags);
    if (!win) {
        std::fprintf(stderr, "SDL_CreateWindow failed: %s\n", SDL_GetError());
        SDL_Quit();
        return 2;
    }
    if (SDL_Surface* icon = make_window_icon()) {
        SDL_SetWindowIcon(win, icon);
        SDL_FreeSurface(icon);
    }
    SDL_Renderer* r =
        SDL_CreateRenderer(win, -1, SDL_RENDERER_ACCELERATED | SDL_RENDERER_PRESENTVSYNC);
    if (!r)
        r = SDL_CreateRenderer(win, -1, SDL_RENDERER_SOFTWARE);
    if (!r) {
        std::fprintf(stderr, "SDL_CreateRenderer failed: %s\n", SDL_GetError());
        SDL_DestroyWindow(win);
        SDL_Quit();
        return 2;
    }
    SDL_SetRenderDrawBlendMode(r, SDL_BLENDMODE_BLEND);
    g_font.load(r); // real system font; silently falls back to the bitmap font if none

    std::optional<std::string> saved_host;
    int rc = 0;
    while (true) {
        auto spec = preflight(win, r, opt, saved_host);
        if (!spec || spec->mode == "quit")
            break;
        if (spec->mode == "pi")
            saved_host = spec->host;
        rc = flight_screen(win, r, *spec, opt);
        if (rc != 0)
            break;
    }
    g_font.clear(); // destroy cached glyph textures before the renderer
    SDL_DestroyRenderer(r);
    SDL_DestroyWindow(win);
    SDL_Quit();
    return rc;
}

} // namespace djilink::gui
