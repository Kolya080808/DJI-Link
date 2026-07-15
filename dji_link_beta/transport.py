"""
Transport abstraction: where DUML bytes physically go to / come from.

The point is to decouple the logic (Drone API, control) from the means of communication.
Today it's AOA-USB, tomorrow a MITM proxy between the phone and the RC, or the network.
Only the Transport implementation changes; the upper layers are untouched.
"""

from __future__ import annotations
import abc


class Transport(abc.ABC):
    @abc.abstractmethod
    def send(self, frame: bytes) -> None: ...

    @abc.abstractmethod
    def recv(self, timeout_ms: int = 1000) -> bytes:
        """Return the raw bytes read (may be b'' on timeout)."""

    def close(self) -> None:
        pass


class LogTransport(Transport):
    """Stub without hardware: prints outgoing frames. For the keyboard/API demo
    and for debugging — a 'rough sanity check' that commands are formed correctly."""

    def __init__(self, verbose: bool = True, silent_repeat: bool = True):
        self.verbose = verbose
        self.silent_repeat = silent_repeat
        self._last = None
        self.sent: list[bytes] = []

    def send(self, frame: bytes) -> None:
        self.sent.append(frame)
        if self.verbose and not (self.silent_repeat and frame == self._last):
            print(f"  TX {frame.hex()}")
        self._last = frame

    def recv(self, timeout_ms: int = 1000) -> bytes:
        return b""


class SerialTransport(Transport):
    """DUML straight into the USB Virtual COM of the RC/drone (e.g. COM4, /dev/ttyACM0).

    The simplest path on a bare laptop: when connected to the PC, the Mavic Mini RC
    comes up as a virtual serial port (VID 2CA3) and accepts DUML without
    initialization (see dji-firmware-tools comm_serialtalk.py). The laptop here is a plain
    USB host. Needs: pip install pyserial.
    """

    def __init__(self, port: str, baudrate: int = 115200):
        try:
            import serial  # pyserial
        except Exception:
            raise RuntimeError("pyserial is not installed:  pip install pyserial")
        # baudrate is ignored by CDC-ACM; don't toggle DTR/RTS to avoid resetting
        self.ser = serial.Serial(port, baudrate, timeout=0.2,
                                 dsrdtr=False, rtscts=False)

    def send(self, frame: bytes) -> None:
        self.ser.write(frame)

    def recv(self, timeout_ms: int = 1000) -> bytes:
        self.ser.timeout = max(0.001, timeout_ms / 1000)
        n = self.ser.in_waiting
        if n:
            return self.ser.read(n)
        return self.ser.read(256)   # blocking wait until timeout

    def close(self) -> None:
        try:
            self.ser.close()
        except Exception:
            pass


class CompositeTransport(Transport):
    """Wrapper for the AOA/Pi path: packs outgoing DUML frames into a composite unit
    (0x5749), returns received data raw (demux is on the client side). Thanks to this
    the Pi stays a dumb jump-host: it just shuffles bytes PC<->RC."""

    def __init__(self, inner: Transport):
        self.inner = inner
        import composite
        self._wrap = composite.wrap

    def send(self, frame: bytes) -> None:
        self.inner.send(self._wrap(frame))

    def recv(self, timeout_ms: int = 1000) -> bytes:
        return self.inner.recv(timeout_ms)

    def close(self) -> None:
        self.inner.close()


class NetTransport(Transport):
    """Laptop transport to the bridge on the Pi (pi/bridge.py) over TCP. Transparently shuffles
    DUML bytes: send() -> to the RC, recv() <- from the RC. This way the laptop (keyboard/Drone
    API/neural net) controls the drone, while all the USB/AOA fuss lives on the Pi."""

    def __init__(self, host: str, port: int = 9910):
        import socket
        self._socket = socket
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.connect((host, port))
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    def send(self, frame: bytes) -> None:
        self.sock.sendall(frame)

    def recv(self, timeout_ms: int = 1000) -> bytes:
        self.sock.settimeout(max(0.001, timeout_ms / 1000))
        try:
            return self.sock.recv(4096)
        except self._socket.timeout:
            return b""
        except OSError:
            return b""

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


class AoaTransport(Transport):
    """Real transport over Android Open Accessory (aoa.open_accessory)."""

    def __init__(self, dev, ep_in, ep_out):
        self.dev = dev
        self.ep_in = ep_in
        self.ep_out = ep_out

    @classmethod
    def connect(cls, model: str | None = None):
        import aoa
        identity = dict(aoa.DJI_IDENTITY)
        if model:
            identity[1] = model
        target = None
        for d in aoa.find_candidate_devices():
            if aoa.get_protocol(d) >= 1:
                target = d
                break
        if target is None:
            raise aoa.AoaError("no device with AOA support (see --scan)")
        aoa.switch_to_accessory(target, identity)
        dev, ep_in, ep_out = aoa.open_accessory()
        return cls(dev, ep_in, ep_out)

    def send(self, frame: bytes) -> None:
        self.dev.write(self.ep_out.bEndpointAddress, frame, timeout=1000)

    def recv(self, timeout_ms: int = 1000) -> bytes:
        try:
            data = self.dev.read(self.ep_in.bEndpointAddress,
                                 self.ep_in.wMaxPacketSize, timeout=timeout_ms)
            return bytes(data)
        except Exception as e:
            if "timeout" in str(e).lower():
                return b""
            raise
