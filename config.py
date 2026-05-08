SERIAL_PORT = "/dev/ttyACM0"
BAUD_RATE = 115200

# Watchdog: PCB declares connection dead if no keep-alive for ~25s
KEEPALIVE_INTERVAL = 20  # seconds

# Hardcoded software safety cutoff — sends Cancel if exceeded
SAFETY_MAX_TEMP_F = 450

# How long to wait for a command acknowledgement frame
COMMAND_TIMEOUT = 5.0  # seconds

# Mock mode — set True to run without hardware (UI development)
MOCK_MODE = False
