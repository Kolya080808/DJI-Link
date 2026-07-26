#include "core/transport.hpp"

#include "core/composite.hpp"

#include <cstdio>
#include <stdexcept>

#ifdef _WIN32
// WIN32_LEAN_AND_MEAN keeps <windows.h> from pulling in the old <winsock.h> (v1),
// so including <winsock2.h> afterwards is safe. clang-format sorts these includes
// alphabetically, which puts <windows.h> first — that ordering only works because
// of WIN32_LEAN_AND_MEAN, so do not remove it.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <winsock2.h>
#include <ws2tcpip.h>
#else
#include <arpa/inet.h>
#include <fcntl.h>
#include <netdb.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <sys/select.h>
#include <sys/socket.h>
#include <termios.h>
#include <unistd.h>
#endif

namespace djilink {
namespace {
#ifdef _WIN32
using sock_t = SOCKET;
constexpr sock_t kBadSock = INVALID_SOCKET;
#else
using sock_t = int;
constexpr sock_t kBadSock = -1;
#endif
} // namespace

// ---------------------------------------------------------------- sockets init
#ifdef _WIN32
namespace {
struct WsaInit {
    WsaInit() {
        WSADATA d;
        WSAStartup(MAKEWORD(2, 2), &d);
    }
    ~WsaInit() {
        WSACleanup();
    }
};
void ensure_wsa() {
    static WsaInit init;
}
} // namespace
#endif

// ---------------------------------------------------------------- LogTransport
LogTransport::LogTransport(bool verbose, bool silent_repeat)
    : verbose_(verbose), silent_repeat_(silent_repeat) {}

void LogTransport::send(const Bytes& frame) {
    if (verbose_ && !(silent_repeat_ && frame == last_)) {
        std::printf("  TX %s\n", to_hex(frame).c_str());
        std::fflush(stdout);
    }
    last_ = frame;
}

Bytes LogTransport::recv(int) {
    return {};
}

// ---------------------------------------------------------------- NetTransport
NetTransport::NetTransport(const std::string& host, int port) {
#ifdef _WIN32
    ensure_wsa();
#endif
    addrinfo hints{};
    hints.ai_family = AF_INET;
    hints.ai_socktype = SOCK_STREAM;
    addrinfo* res = nullptr;
    const std::string ports = std::to_string(port);
    if (getaddrinfo(host.c_str(), ports.c_str(), &hints, &res) != 0 || !res) {
        throw std::runtime_error("NetTransport: cannot resolve " + host);
    }
    sock_t s = ::socket(res->ai_family, res->ai_socktype, res->ai_protocol);
    if (s == kBadSock) {
        freeaddrinfo(res);
        throw std::runtime_error("NetTransport: socket() failed");
    }
    if (::connect(s, res->ai_addr, static_cast<int>(res->ai_addrlen)) != 0) {
        freeaddrinfo(res);
#ifdef _WIN32
        ::closesocket(s);
#else
        ::close(s);
#endif
        throw std::runtime_error("NetTransport: connect to " + host + " failed");
    }
    freeaddrinfo(res);
    int one = 1;
    ::setsockopt(s, IPPROTO_TCP, TCP_NODELAY, reinterpret_cast<const char*>(&one), sizeof(one));
    fd_ = static_cast<std::intptr_t>(s);
}

NetTransport::~NetTransport() {
    close();
}

void NetTransport::send(const Bytes& frame) {
    if (fd_ < 0)
        return;
    const sock_t s = static_cast<sock_t>(fd_);
    std::size_t sent = 0;
    while (sent < frame.size()) {
        const int n = static_cast<int>(::send(s, reinterpret_cast<const char*>(frame.data() + sent),
                                              static_cast<int>(frame.size() - sent), 0));
        if (n <= 0)
            return; // link gone — quietly drop (matches Python's sendall failure path)
        sent += static_cast<std::size_t>(n);
    }
}

Bytes NetTransport::recv(int timeout_ms) {
    if (fd_ < 0)
        return {};
    const sock_t s = static_cast<sock_t>(fd_);
    fd_set rf;
    FD_ZERO(&rf);
    FD_SET(s, &rf);
    timeval tv;
    const int ms = timeout_ms < 1 ? 1 : timeout_ms;
    tv.tv_sec = ms / 1000;
    tv.tv_usec = (ms % 1000) * 1000;
    const int r = ::select(static_cast<int>(s) + 1, &rf, nullptr, nullptr, &tv);
    if (r <= 0)
        return {}; // timeout or error
    char buf[4096];
    const int n = static_cast<int>(::recv(s, buf, sizeof(buf), 0));
    if (n <= 0)
        return {};
    return Bytes(reinterpret_cast<std::uint8_t*>(buf), reinterpret_cast<std::uint8_t*>(buf) + n);
}

void NetTransport::close() {
    if (fd_ < 0)
        return;
    const sock_t s = static_cast<sock_t>(fd_);
#ifdef _WIN32
    ::closesocket(s);
#else
    ::close(s);
#endif
    fd_ = -1;
}

// ------------------------------------------------------------ CompositeTransport
CompositeTransport::CompositeTransport(std::unique_ptr<Transport> inner)
    : inner_(std::move(inner)) {}

void CompositeTransport::send(const Bytes& frame) {
    inner_->send(composite_wrap(frame));
}
Bytes CompositeTransport::recv(int timeout_ms) {
    return inner_->recv(timeout_ms);
}
void CompositeTransport::close() {
    inner_->close();
}

// --------------------------------------------------------------- SerialTransport
#ifdef _WIN32
SerialTransport::SerialTransport(const std::string& port, int /*baudrate*/) {
    // Prefix \\.\ so ports above COM9 work too.
    std::string path = (port.rfind("\\\\.\\", 0) == 0) ? port : ("\\\\.\\" + port);
    HANDLE h = ::CreateFileA(path.c_str(), GENERIC_READ | GENERIC_WRITE, 0, nullptr, OPEN_EXISTING,
                             0, nullptr);
    if (h == INVALID_HANDLE_VALUE) {
        throw std::runtime_error("SerialTransport: cannot open " + port);
    }
    DCB dcb{};
    dcb.DCBlength = sizeof(dcb);
    ::GetCommState(h, &dcb);
    dcb.BaudRate = CBR_115200; // ignored by CDC-ACM
    dcb.ByteSize = 8;
    dcb.Parity = NOPARITY;
    dcb.StopBits = ONESTOPBIT;
    dcb.fDtrControl = DTR_CONTROL_DISABLE; // don't toggle DTR/RTS -> no reset
    dcb.fRtsControl = RTS_CONTROL_DISABLE;
    ::SetCommState(h, &dcb);
    handle_ = h;
}

SerialTransport::~SerialTransport() {
    close();
}

void SerialTransport::send(const Bytes& frame) {
    if (!handle_)
        return;
    DWORD written = 0;
    ::WriteFile(static_cast<HANDLE>(handle_), frame.data(), static_cast<DWORD>(frame.size()),
                &written, nullptr);
}

Bytes SerialTransport::recv(int timeout_ms) {
    if (!handle_)
        return {};
    COMMTIMEOUTS to{};
    to.ReadIntervalTimeout = 20;
    to.ReadTotalTimeoutConstant = static_cast<DWORD>(timeout_ms < 1 ? 1 : timeout_ms);
    to.ReadTotalTimeoutMultiplier = 0;
    ::SetCommTimeouts(static_cast<HANDLE>(handle_), &to);
    char buf[4096];
    DWORD n = 0;
    if (!::ReadFile(static_cast<HANDLE>(handle_), buf, sizeof(buf), &n, nullptr) || n == 0) {
        return {};
    }
    return Bytes(reinterpret_cast<std::uint8_t*>(buf), reinterpret_cast<std::uint8_t*>(buf) + n);
}

void SerialTransport::close() {
    if (handle_) {
        ::CloseHandle(static_cast<HANDLE>(handle_));
        handle_ = nullptr;
    }
}

#else  // POSIX serial
SerialTransport::SerialTransport(const std::string& port, int /*baudrate*/) {
    fd_ = ::open(port.c_str(), O_RDWR | O_NOCTTY | O_NONBLOCK);
    if (fd_ < 0) {
        throw std::runtime_error("SerialTransport: cannot open " + port);
    }
    termios tio{};
    if (::tcgetattr(fd_, &tio) == 0) {
        cfmakeraw(&tio);
        tio.c_cflag |= (CLOCAL | CREAD);
        tio.c_cflag &= ~CRTSCTS; // no hardware flow control
        tio.c_cc[VMIN] = 0;
        tio.c_cc[VTIME] = 2; // 0.2s read granularity (baudrate ignored by CDC-ACM)
        ::tcsetattr(fd_, TCSANOW, &tio);
    }
    // Back to blocking reads gated by VTIME/select.
    int fl = ::fcntl(fd_, F_GETFL, 0);
    ::fcntl(fd_, F_SETFL, fl & ~O_NONBLOCK);
}

SerialTransport::~SerialTransport() {
    close();
}

void SerialTransport::send(const Bytes& frame) {
    if (fd_ < 0)
        return;
    std::size_t off = 0;
    while (off < frame.size()) {
        const ssize_t n = ::write(fd_, frame.data() + off, frame.size() - off);
        if (n <= 0)
            return;
        off += static_cast<std::size_t>(n);
    }
}

Bytes SerialTransport::recv(int timeout_ms) {
    if (fd_ < 0)
        return {};
    fd_set rf;
    FD_ZERO(&rf);
    FD_SET(fd_, &rf);
    timeval tv;
    const int ms = timeout_ms < 1 ? 1 : timeout_ms;
    tv.tv_sec = ms / 1000;
    tv.tv_usec = (ms % 1000) * 1000;
    if (::select(fd_ + 1, &rf, nullptr, nullptr, &tv) <= 0)
        return {};
    std::uint8_t buf[4096];
    const ssize_t n = ::read(fd_, buf, sizeof(buf));
    if (n <= 0)
        return {};
    return Bytes(buf, buf + n);
}

void SerialTransport::close() {
    if (fd_ >= 0) {
        ::close(fd_);
        fd_ = -1;
    }
}
#endif // _WIN32

} // namespace djilink
