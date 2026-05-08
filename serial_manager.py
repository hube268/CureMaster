"""
Serial manager for CureMaster.
Owns the /dev/ttyACM0 connection, frame parsing, command sending,
and the keep-alive watchdog thread.
"""
import re
import threading
import time
import logging
from dataclasses import dataclass
from typing import Callable, Optional

import serial
import serial.serialutil

from config import (
    SERIAL_PORT, BAUD_RATE, KEEPALIVE_INTERVAL, SAFETY_MAX_TEMP_F
)

log = logging.getLogger(__name__)


@dataclass
class OvenFrame:
    state: str = "IDLE"
    duration: str = "0:00:00"
    target_f: float = 0.0
    avg_temp_c: float = 0.0
    tc1_c: float = 0.0
    tc2_c: float = 0.0
    ambient_f: float = 0.0
    ssr: bool = False
    fan: bool = False
    door_closed: bool = False
    lights: bool = False
    pid: float = 0.0
    stay_warm_min: int = 30
    raw: str = ""

    @property
    def avg_temp_f(self) -> float:
        return self.avg_temp_c * 9.0 / 5.0 + 32.0

    @property
    def is_active(self) -> bool:
        return self.state not in ("IDLE", "COOLDOWN", "")


# Regex for the temperature field: "56.52 (56.30, 57.20) "
_TEMP_RE = re.compile(r'([\d.]+)\s+\(([\d.]+),\s*([\d.]+)\)')


def parse_frame(line: str) -> Optional[OvenFrame]:
    """Parse a raw status frame string into an OvenFrame. Returns None on failure."""
    parts = line.strip("<>").split("|")
    if len(parts) < 14:
        return None
    try:
        m = _TEMP_RE.match(parts[4].strip())
        if not m:
            return None
        return OvenFrame(
            state=parts[0],
            duration=parts[1],
            target_f=float(parts[3]),
            avg_temp_c=float(m.group(1)),
            tc1_c=float(m.group(2)),
            tc2_c=float(m.group(3)),
            ambient_f=float(parts[5]),
            ssr=parts[6] == "1",
            fan=parts[7] == "1",
            lights=parts[9] == "1",
            pid=float(parts[10]),
            stay_warm_min=int(parts[12]),
            door_closed=parts[13] == "1",
            raw=line,
        )
    except (ValueError, IndexError, AttributeError):
        return None


class SerialManager:
    """
    Manages the serial connection to the cure PCB.

    Usage:
        mgr = SerialManager()
        mgr.on_frame = lambda f: print(f.state, f.avg_temp_f)
        mgr.connect()
        mgr.send_command("LightOn")
        mgr.disconnect()
    """

    def __init__(self, port: str = SERIAL_PORT, baud: int = BAUD_RATE):
        self._port = port
        self._baud = baud
        self._ser: Optional[serial.Serial] = None
        self._lock = threading.Lock()
        self._running = False
        self._read_thread: Optional[threading.Thread] = None
        self._keepalive_thread: Optional[threading.Thread] = None

        self.last_frame: Optional[OvenFrame] = None
        self.on_frame: Optional[Callable[[OvenFrame], None]] = None
        self.on_error: Optional[Callable[[Exception], None]] = None

    # ── Public API ───────────────────────────────────────────────

    def connect(self):
        """Open serial port and start background threads."""
        try:
            self._ser = serial.Serial(self._port, self._baud, timeout=2)
        except serial.serialutil.SerialException as e:
            raise ConnectionError(f"Cannot open {self._port}: {e}") from e

        time.sleep(0.3)
        self._ser.reset_input_buffer()
        self._send_raw(b"\x18")  # soft reset
        time.sleep(0.5)

        self._running = True
        self._read_thread = threading.Thread(target=self._read_loop, daemon=True, name="serial-reader")
        self._keepalive_thread = threading.Thread(target=self._keepalive_loop, daemon=True, name="keepalive")
        self._read_thread.start()
        self._keepalive_thread.start()
        log.info("Connected to %s at %d baud", self._port, self._baud)

    def disconnect(self):
        """Stop threads and close port."""
        self._running = False
        if self._ser and self._ser.is_open:
            self._ser.close()
        log.info("Disconnected from %s", self._port)

    def send_command(self, cmd: str):
        """
        Send a framed command: <cmd>\\n
        Examples: send_command("LightOn"), send_command("SetTemp=400")
        """
        payload = f"<{cmd}>\n".encode()
        self._send_raw(payload)
        log.debug("TX: %s", cmd)

    # ── Private ──────────────────────────────────────────────────

    def _send_raw(self, data: bytes):
        with self._lock:
            if self._ser and self._ser.is_open:
                self._ser.write(data)
                self._ser.flush()

    def _read_loop(self):
        while self._running:
            try:
                line = self._ser.readline().decode("utf-8", errors="replace").strip()
                if not (line.startswith("<") and "|" in line):
                    continue
                frame = parse_frame(line)
                if frame is None:
                    continue
                self.last_frame = frame
                self._safety_check(frame)
                if self.on_frame:
                    self.on_frame(frame)
            except Exception as e:
                if self._running:
                    log.error("Read error: %s", e)
                    if self.on_error:
                        self.on_error(e)
                    time.sleep(1)

    def _keepalive_loop(self):
        while self._running:
            time.sleep(KEEPALIVE_INTERVAL)
            if self._running:
                self._send_raw(b"<?>\n")
                log.debug("TX: keepalive")

    def _safety_check(self, frame: OvenFrame):
        if frame.avg_temp_f > SAFETY_MAX_TEMP_F:
            log.critical(
                "SAFETY CUTOFF triggered: %.1f°F > %.1f°F limit — sending Cancel",
                frame.avg_temp_f, SAFETY_MAX_TEMP_F,
            )
            self.send_command("Cancel")
