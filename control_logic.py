"""
Oven controller — high-level API over SerialManager.
Translates user intent into PCB command sequences.
"""
import logging
from typing import Optional, Union

from config import MOCK_MODE
from serial_manager import OvenFrame

if MOCK_MODE:
    from mock_serial import MockSerialManager as _Manager
else:
    from serial_manager import SerialManager as _Manager

log = logging.getLogger(__name__)


class OvenController:
    """
    High-level oven control API.

    Typical bake flow:
        oven = OvenController()
        oven.connect()
        oven.set_units_imperial()
        oven.set_temperature(400)
        oven.set_duration(hours=1, mins=0, secs=0)
        oven.start_warmup()
        # wait for on_state_change callback with state="AT TEMP"
        oven.start_cure()
        # wait for on_state_change callback with state="COOLDOWN"
        oven.disconnect()
    """

    def __init__(self):
        self._mgr = _Manager()
        self._mgr.on_frame = self._on_frame
        self._mgr.on_error = self._on_error

        self._last_state: Optional[str] = None
        self.on_frame: Optional[callable] = None
        self.on_state_change: Optional[callable] = None

    # ── Lifecycle ────────────────────────────────────────────────

    def connect(self):
        self._mgr.connect()

    def disconnect(self):
        self._mgr.disconnect()

    @property
    def last_frame(self) -> Optional[OvenFrame]:
        return self._mgr.last_frame

    # ── Bake control ─────────────────────────────────────────────

    def set_units_imperial(self):
        self._mgr.send_command("UnitImperial")

    def set_temperature(self, temp_f: float):
        """Set target temperature in °F."""
        self._mgr.send_command(f"SetTemp={temp_f:.0f}")
        log.info("Set temperature: %.0f°F", temp_f)

    def set_duration(self, hours: int = 0, mins: int = 0, secs: int = 0):
        """Set cure duration."""
        self._mgr.send_command(f"SetTime={hours:02d}:{mins:02d}:{secs:02d}")
        log.info("Set duration: %02d:%02d:%02d", hours, mins, secs)

    def start_warmup(self):
        """Begin heating to setpoint (IDLE → WARMING UP)."""
        self._mgr.send_command("WarmUp")
        log.info("Warmup started")

    def start_cure(self):
        """Start cure timer (AT TEMP → CURING)."""
        self._mgr.send_command("Start")
        log.info("Cure timer started")

    def cancel(self):
        """Cancel bake and return to IDLE."""
        self._mgr.send_command("Cancel")
        log.info("Bake cancelled")

    # ── Accessories ──────────────────────────────────────────────

    def set_lights(self, on: bool):
        self._mgr.send_command("LightOn" if on else "LightOff")

    def set_fan(self, on: bool):
        self._mgr.send_command("FanOn" if on else "FanOff")

    def set_fan_speed(self, speed: str):
        """speed: 'LOW' | 'MEDIUM' | 'HIGH'"""
        if speed not in ("LOW", "MEDIUM", "HIGH"):
            raise ValueError(f"Invalid fan speed: {speed}")
        self._mgr.send_command(f"FanSpeed={speed}")

    # ── Stay-warm ────────────────────────────────────────────────

    def set_stay_warm(self, enabled: bool, minutes: int = 30):
        self._mgr.send_command("StayOnEnable" if enabled else "StayOnDisable")
        if enabled:
            self._mgr.send_command(f"StayWarm{minutes}")

    # ── Housekeeping ─────────────────────────────────────────────

    def clear_error(self):
        self._mgr.send_command("ClearError")

    def reset_machine(self):
        self._mgr.send_command("RESET")

    # ── Internal ─────────────────────────────────────────────────

    def _on_frame(self, frame: OvenFrame):
        if frame.state != self._last_state:
            log.info("State: %s → %s", self._last_state, frame.state)
            self._last_state = frame.state
            if self.on_state_change:
                self.on_state_change(frame.state, frame)
        if self.on_frame:
            self.on_frame(frame)

    def _on_error(self, exc: Exception):
        log.error("Serial error: %s", exc)
