# CureMaster — PCB Serial Protocol Reference

Discovered via Stage 2 hardware validation (SSH to Orange Pi 3B at 10.124.1.167)
and decompilation of Langmuir Systems `curecontrol.jar` (machine ID: LS-CURE, firmware 1.0).

---

## Connection

- Port: `/dev/ttyACM0`
- Baud: `115200`, 8N1
- On connect: send `0x18` (soft reset byte, GRBL Ctrl-X)
- Keep-alive: send `<?>\n` every **≤25 seconds** or PCB considers connection dead

---

## Status Frame Format

PCB streams frames at ~4 Hz (250 ms interval):

```
<STATE|HH:MM:SS|1|TARGET_F|AVG_C (TC1_C, TC2_C) |AMBIENT_F|SSR|FAN|?|LIGHTS|PID|1|STAY_WARM_MIN|DOOR>
```

### Live example
```
<IDLE|0:00:00|1|300|56.52 (56.30, 57.20) |63.89|0|0|0|0|0.00|1|30|0>
```

### Field index table

| Index | Name          | Type    | Notes                                      |
|-------|---------------|---------|--------------------------------------------|
| 0     | STATE         | string  | IDLE / WARMING UP / AT TEMP / CURING / COOLDOWN |
| 1     | Duration      | string  | HH:MM:SS elapsed cure time                 |
| 2     | —             | int     | constant 1 (protocol version)              |
| 3     | Target        | float   | setpoint in **°F**                         |
| 4     | Temperature   | string  | `AVG_°C (TC1_°C, TC2_°C)` — note **°C**   |
| 5     | Ambient       | float   | ambient in **°F**                          |
| 6     | SSR           | 0/1     | heater relay                               |
| 7     | Fan           | 0/1     | fan active                                 |
| 8     | —             | 0/1     | unknown, always 0 in all tests             |
| 9     | Lights        | 0/1     | oven light                                 |
| 10    | PID           | float   | PID output                                 |
| 11    | —             | int     | constant 1                                 |
| 12    | Stay-warm     | int     | stay-warm minutes (default 30)             |
| 13    | Door          | 0/1     | **0 = open, 1 = closed** (limit switch)    |

### Temperature field parsing (index 4)
```python
import re
m = re.match(r'([\d.]+)\s+\(([\d.]+),\s*([\d.]+)\)', parts[4].strip())
avg_c, tc1_c, tc2_c = float(m.group(1)), float(m.group(2)), float(m.group(3))
```

---

## Command Framing

All commands use angle-bracket + newline framing:
```
<COMMAND>\n
```
Bare commands (e.g. `LightOn\n` without brackets) are **ignored** by the PCB.

Source: constant pool entry `#819 = String "<>\n"` in `CureMachineHandlerBase.class`
(StringConcatFactory template where `` is the substitution slot).

---

## Complete Command Reference

### Bake control

| Command           | Serial bytes          | Source class               |
|-------------------|-----------------------|----------------------------|
| Set temperature   | `<SetTemp=400>\n`     | `CureMachineHandler` key=`Temp`, fmt=`%.0f` |
| Set duration      | `<SetTime=01:00:00>\n`| `CureMachineHandler` key=`Time`, fmt=`%02d:%02d:%02d` |
| Begin warm-up     | `<WarmUp>\n`          | `PerformWarmUpOperation`   |
| Start cure timer  | `<Start>\n`           | `PerformStartOperation`    |
| Cancel bake       | `<Cancel>\n`          | `PerformCancelOperation`   |

### Accessories

| Command           | Serial bytes              | Source class                    |
|-------------------|---------------------------|---------------------------------|
| Light on          | `<LightOn>\n`             | `UpdateLightEnableOperation` ✅  |
| Light off         | `<LightOff>\n`            | `UpdateLightEnableOperation` ✅  |
| Fan on            | `<FanOn>\n`               | `UpdateFanEnableOperation` ✅    |
| Fan off           | `<FanOff>\n`              | `UpdateFanEnableOperation` ✅    |
| Fan speed low     | `<FanSpeed=LOW>\n`        | `UpdateFanLevelOperation`       |
| Fan speed medium  | `<FanSpeed=MEDIUM>\n`     | `UpdateFanLevelOperation`       |
| Fan speed high    | `<FanSpeed=HIGH>\n`       | `UpdateFanLevelOperation`       |

### Stay-warm

| Command                | Serial bytes          |
|------------------------|-----------------------|
| Stay-warm enable       | `<StayOnEnable>\n`    |
| Stay-warm disable      | `<StayOnDisable>\n`   |
| Stay-warm set minutes  | `<StayWarm30>\n`      |

### Units & housekeeping

| Command                | Serial bytes          |
|------------------------|-----------------------|
| Units imperial (°F)    | `<UnitImperial>\n`    |
| Units metric (°C)      | `<UnitMetric>\n`      |
| Keep-alive watchdog    | `<?>\n`               |
| Soft reset (on connect)| `b'\x18'`             |
| Machine reset          | `<RESET>\n`           |
| Clear error            | `<ClearError>\n`      |
| Request EEPROM settings| `<$#>\n`              |

### PCB acknowledgement messages

The PCB echoes received commands as:
```
[RECEIVED STRING: CommandName]
```

### EEPROM settings frames

PCB sends EEPROM/settings frames prefixed with `$MT=` (separate from status frames).

---

## State Machine

```
IDLE  ──[WarmUp]──►  WARMING UP  ──[at setpoint]──►  AT TEMP  ──[Start]──►  CURING
                                                                                │
                                                                          [timer done]
                                                                                │
IDLE  ◄──────────────────────────────────────────────────────────────  COOLDOWN
```

Any state → `<Cancel>\n` → IDLE

---

## Bake sequence (full)

```python
send("<UnitImperial>")       # set units to °F
send("<SetTemp=400>")        # temperature setpoint
send("<SetTime=01:00:00>")   # cure duration
send("<WarmUp>")             # begin heating
# poll frames until state == "AT TEMP"
send("<Start>")              # start cure timer
# poll frames until state == "COOLDOWN" or "IDLE"
# send "<?>" every 20s throughout
```

---

## Hardware notes

- **Original platform**: Orange Pi 3B, SSH `cnc@10.124.1.167`, password `cnc`
- **Migration target**: Raspberry Pi 4, Raspberry Pi OS Bookworm 64-bit
- **Java app restart** (Orange Pi): requires `DISPLAY=:0` in environment
  ```bash
  export DISPLAY=:0; cd /home/cnc && nohup bash start.sh &
  ```
- **JAR location**: `/data/curecontrol.jar` (copied to `/tmp/bendcontrol.jar` at startup)
- **pyserial**: installed on Orange Pi as `python3-serial` v3.5

---

## Safety

Hardcoded software cutoff: send `<Cancel>\n` if thermocouple avg exceeds `SAFETY_MAX_TEMP_F`.
The PCB owns the PID loop; the Pi is a thin client that sends setpoints and monitors state.
