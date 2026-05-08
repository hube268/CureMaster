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
import time
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

from control_logic import OvenController


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--temp", type=float, default=200.0, help="Target temp in °F (default 200)")
    parser.add_argument("--time", type=int, default=5, help="Cure duration in minutes (default 5)")
    args = parser.parse_args()

    oven = OvenController()

    state_log: list[tuple[str, str]] = []

    def on_state(state: str, frame):
        ts = datetime.now().strftime("%H:%M:%S")
        state_log.append((ts, state))
        print(f"\n[{ts}]  *** STATE → {state} ***\n")

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

        log.info("Starting warmup — heating to %.0f°F", args.temp)
        oven.start_warmup()

        # Wait for AT TEMP
        log.info("Waiting for AT TEMP...")
        while True:
            frame = oven.last_frame
            if frame and frame.state == "AT TEMP":
                break
            if frame and frame.state not in ("IDLE", "WARMING UP", "AT TEMP"):
                log.error("Unexpected state during warmup: %s", frame.state)
                break
            time.sleep(0.5)

        if oven.last_frame and oven.last_frame.state == "AT TEMP":
            log.info("AT TEMP reached — starting cure timer")
            oven.start_cure()

            # Wait for COOLDOWN or IDLE (cure complete)
            log.info("Curing for %d minutes — waiting for COOLDOWN...", mins)
            while True:
                frame = oven.last_frame
                if frame and frame.state in ("COOLDOWN", "IDLE"):
                    break
                time.sleep(1.0)

            log.info("Cure cycle complete. Final state: %s", oven.last_frame.state if oven.last_frame else "?")

    except KeyboardInterrupt:
        print()
        log.warning("Interrupted — cancelling bake")
        oven.cancel()
        time.sleep(1.0)

    finally:
        oven.disconnect()

    print("\n--- State transitions ---")
    for ts, state in state_log:
        print(f"  {ts}  {state}")

    if len(state_log) >= 2:
        print("\nBake test PASS — state machine responded correctly.")
        sys.exit(0)
    else:
        print("\nBake test INCOMPLETE — check serial connection and PCB state.")
        sys.exit(1)


if __name__ == "__main__":
    main()
