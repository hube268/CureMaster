"""
Mock serial manager for UI development without hardware.
Simulates PCB state machine and responds to commands.
"""
import threading
import time
import logging
from typing import Callable, Optional

from serial_manager import OvenFrame

log = logging.getLogger(__name__)

_TICK = 0.25  # seconds between frames (matches real PCB ~4 Hz)


class MockSerialManager:
    """
    Drop-in replacement for SerialManager when MOCK_MODE=True.
    Simulates warming up, reaching temp, curing, and cooldown.
    """

    def __init__(self, **_kwargs):
        self.last_frame: Optional[OvenFrame] = None
        self.on_frame: Optional[Callable[[OvenFrame], None]] = None
        self.on_error: Optional[Callable[[Exception], None]] = None

        self._running = False
        self._thread: Optional[threading.Thread] = None

        # Simulated machine state
        self._state = "IDLE"
        self._target_f = 300.0
        self._current_c = 22.0  # room temp ~72°F
        self._fan = False
        self._lights = False
        self._door_closed = True
        self._cure_seconds = 0
        self._cure_duration = 3600  # 1 hour default
        self._elapsed = 0

    # ── Public API (mirrors SerialManager) ──────────────────────

    def connect(self):
        self._running = True
        self._thread = threading.Thread(target=self._sim_loop, daemon=True, name="mock-sim")
        self._thread.start()
        log.info("MockSerial connected")

    def disconnect(self):
        self._running = False
        log.info("MockSerial disconnected")

    def send_command(self, cmd: str):
        log.debug("Mock RX: %s", cmd)
        self._handle_command(cmd)

    # ── Command handling ─────────────────────────────────────────

    def _handle_command(self, cmd: str):
        if cmd == "LightOn":
            self._lights = True
        elif cmd == "LightOff":
            self._lights = False
        elif cmd == "FanOn":
            self._fan = True
        elif cmd == "FanOff":
            self._fan = False
        elif cmd.startswith("SetTemp="):
            try:
                self._target_f = float(cmd.split("=", 1)[1])
            except ValueError:
                pass
        elif cmd.startswith("SetTime="):
            try:
                parts = cmd.split("=", 1)[1].split(":")
                h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
                self._cure_duration = h * 3600 + m * 60 + s
            except (ValueError, IndexError):
                pass
        elif cmd == "WarmUp":
            if self._state == "IDLE":
                self._state = "WARMING UP"
                self._elapsed = 0
        elif cmd == "Start":
            if self._state == "AT TEMP":
                self._state = "CURING"
                self._cure_seconds = 0
        elif cmd == "Cancel":
            self._state = "IDLE"
            self._elapsed = 0
            self._cure_seconds = 0

    # ── Simulation loop ──────────────────────────────────────────

    def _sim_loop(self):
        target_c = self._target_f_to_c()
        while self._running:
            time.sleep(_TICK)
            target_c = self._target_f_to_c()

            if self._state == "WARMING UP":
                self._elapsed += _TICK
                # Heat at ~5°C per second towards target
                if self._current_c < target_c - 1:
                    self._current_c = min(self._current_c + 5 * _TICK, target_c)
                else:
                    self._state = "AT TEMP"
                    log.info("Mock: AT TEMP (%.1f°C)", self._current_c)

            elif self._state == "AT TEMP":
                self._current_c = target_c  # hold

            elif self._state == "CURING":
                self._elapsed += _TICK
                self._cure_seconds += _TICK
                self._current_c = target_c  # hold during cure
                if self._cure_seconds >= self._cure_duration:
                    self._state = "COOLDOWN"
                    log.info("Mock: COOLDOWN")

            elif self._state == "COOLDOWN":
                self._elapsed += _TICK
                if self._current_c > 30:
                    self._current_c = max(self._current_c - 2 * _TICK, 30.0)
                else:
                    self._state = "IDLE"
                    self._elapsed = 0
                    log.info("Mock: IDLE")

            frame = self._make_frame()
            self.last_frame = frame
            if self.on_frame:
                self.on_frame(frame)

    def _target_f_to_c(self) -> float:
        return (self._target_f - 32.0) * 5.0 / 9.0

    def _make_frame(self) -> OvenFrame:
        elapsed_h = int(self._elapsed // 3600)
        elapsed_m = int((self._elapsed % 3600) // 60)
        elapsed_s = int(self._elapsed % 60)
        duration_str = f"{elapsed_h}:{elapsed_m:02d}:{elapsed_s:02d}"

        tc2 = self._current_c + 0.3  # slight thermocouple spread
        ambient_f = 68.0
        ssr = self._state in ("WARMING UP", "AT TEMP", "CURING")
        pid = 1.0 if ssr else 0.0

        return OvenFrame(
            state=self._state,
            duration=duration_str,
            target_f=self._target_f,
            avg_temp_c=self._current_c,
            tc1_c=self._current_c,
            tc2_c=tc2,
            ambient_f=ambient_f,
            ssr=ssr,
            fan=self._fan,
            door_closed=self._door_closed,
            lights=self._lights,
            pid=pid,
            stay_warm_min=30,
            raw=f"<MOCK|{duration_str}|1|{self._target_f:.0f}|{self._current_c:.2f}|...|>",
        )
