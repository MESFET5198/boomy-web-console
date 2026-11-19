# Boomy Web Console

A lightweight, zero-dependency bilingual (中文 / English) Web control console and telemetry dashboard for the **Noetix Boomy** humanoid robot (`HighController` via `noetix_sdk_boomy`).

## Features

- **Zero External Dependencies**: Built purely with Python standard library (`ThreadingHTTPServer`) and native HTML/CSS/JS.
- **Bilingual UI (中文 / EN)**: One-click language toggle in the header with `localStorage` persistence.
- **Motion Control**: Virtual joystick, D-pad buttons, keyboard (`WASD` + `Q/E`), speed scale slider, and a `0.6s` velocity watchdog.
- **State Machine & Preset Actions**:
  - `START` (Enable/Disable), `SWITCH` (Ready), `WALK`, `FULLWALK`, `FALLTOSTAND`, `STANDTOFALL`, `DEFAULT`
  - Presets: `SWING`, `SHAKE`, `CHEER`, `ZHOUJI`, `DANCE0/1/2`
  - Teach mode: `STARTTEACH`, `ENDTEACH`, `SAVETEACH`, `PLAYTEACH` with teach index
  - Edge-triggered command queue (50Hz control loop, >= 2ms interval)
- **Live Telemetry**: Connection status, `workmode`, BMS battery (`SOC`, `SOH`, temperature), IMU (`ori`, `angular_vel`, `linear_acc`), and all 21 joint motors (`pos`, `vel`, `tau`, `temperature`, `error`).

## Prerequisites

1. Clone and build [noetix_sdk_boomy](https://github.com/Noetix-Robotics/noetix_sdk_boomy):
   ```bash
   git clone https://github.com/Noetix-Robotics/noetix_sdk_boomy.git
   cd noetix_sdk_boomy
   ./build.sh
   ```
2. Ensure your Linux host (or WSL2 in Bridged Mode) has IP `192.168.55.101/24` and can reach the robot motion controller at `192.168.55.102`:
   ```bash
   ./setup_ip.sh
   ```

## Usage

Place `web/` inside `noetix_sdk_boomy/` (or pass `--sdk-dir /path/to/noetix_sdk_boomy`), then run:

```bash
python3 web/server.py --host 0.0.0.0 --port 8080
```

Open `http://192.168.55.101:8080` in your browser.
