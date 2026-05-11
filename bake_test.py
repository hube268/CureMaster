"""
Stage 3 bake test — validates the full WarmUp → AT TEMP → Start → CURING sequence.
Run on the Raspberry Pi with the PCB connected via /dev/ttyACM0.

Usage:
    python3 bake_test.py [--temp TEMP_F] [--time MINUTES]

Defaults: 200°F for 5 minutes (safe low-temp validation run).
Press Ctrl+C at any time to cancel and return the oven to IDLE.
"""
import argparse
import logging
import sys
import threading
import time
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

from control_logic import OvenController

WARMUP_TIMEOUT_MIN = 60
CURE_TIMEOUT_EXTRA_MIN = 10  # extra headroom beyond the requested cure time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--temp", type=float, default=200.0, help="Target temp in °F (default 200)")
    parser.add_argument("--time", type=int, default=5, help="Cure duration in minutes (default 5)")
    args = parser.parse_args()

    oven = OvenController()

    state_log: list[tuple[str, str]] = []

    # Events set by the state-change callback — never missed regardless of how
    # briefly the PCB stays in a given state.
    at_temp_event = threading.Event()
    cure_done_event = threading.Event()

    def on_state(state: str, frame):
        ts = datetime.now().strftime("%H:%M:%S")
        state_log.append((ts, state))
        print(f"\n[{ts}]  *** STATE → {state} ***\n")
        if state == "AT TEMP":
            at_temp_event.set()
        if state in ("COOLDOWN", "IDLE"):
            cure_done_event.set()

    def on_frame(frame):
        print(
            f"\r  {frame.state:<12}  "
            f"avg={frame.avg_temp_f:6.1f}°F  "
            f"target={frame.target_f:.0f}°F  "
            f"tc1={frame.tc1_c:.1f}°C  tc2={frame.tc2_c:.1f}°C  "
            f"ssr={int(frame.ssr)}  fan={int(frame.fan)}  "
            f"door={'CLOSED' if frame.door_closed else 'OPEN '}",
            end="",
            flush=True,
        )

    oven.on_state_change = on_state
    oven.on_frame = on_frame

    try:
        log.info("Connecting to PCB...")
        oven.connect()
        time.sleep(1.0)

        log.info("Setting units to imperial")
        oven.set_units_imperial()
        time.sleep(0.5)

        log.info("Setting temperature: %.0f°F", args.temp)
        oven.set_temperature(args.temp)
        time.sleep(0.5)

        mins = args.time
        log.info("Setting duration: %d minutes", mins)
        oven.set_duration(hours=0, mins=mins, secs=0)
        time.sleep(0.5)

        log.info("Turning fan on")
        oven.set_fan(True)
        time.sleep(0.5)

        log.info("Starting warmup — heating to %.0f°F", args.temp)
        oven.start_warmup()

        log.info("Waiting for AT TEMP (timeout %d min)...", WARMUP_TIMEOUT_MIN)
        reached = at_temp_event.wait(timeout=WARMUP_TIMEOUT_MIN * 60)

        if not reached:
            log.error("Timed out waiting for AT TEMP after %d minutes", WARMUP_TIMEOUT_MIN)
            oven.cancel()
        else:
            log.info("AT TEMP reached — starting cure timer")
            oven.start_cure()

            cure_timeout = (mins + CURE_TIMEOUT_EXTRA_MIN) * 60
            log.info("Curing for %d min — waiting for COOLDOWN (timeout %d min)...",
                     mins, mins + CURE_TIMEOUT_EXTRA_MIN)
            finished = cure_done_event.wait(timeout=cure_timeout)

            if not finished:
                log.error("Timed out waiting for COOLDOWN after %d minutes", mins + CURE_TIMEOUT_EXTRA_MIN)
                oven.cancel()
            else:
                log.info("Cure cycle complete. Final state: %s",
                         oven.last_frame.state if oven.last_frame else "?")

    except KeyboardInterrupt:
        print()
        log.warning("Interrupted — cancelling bake")
        oven.cancel()
        time.sleep(1.0)

    finally:
        oven.disconnect()

    reached_states = {s for _, s in state_log}
    required = {"WARMING UP", "AT TEMP", "CURING"}
    missing = required - reached_states

    print("\n--- State transitions ---")
    for ts, state in state_log:
        print(f"  {ts}  {state}")

    if not missing:
        print("\nBake test PASS — all required states reached.")
        sys.exit(0)
    else:
        print(f"\nBake test INCOMPLETE — missing states: {', '.join(sorted(missing))}")
        sys.exit(1)


if __name__ == "__main__":
    main()
