# EHR-Recorder

Hongrun T9110 Paperless Recorder - Data Acquisition & Cloud Map Visualization  
虹润 T9110 无纸记录仪 - 数据采集与温度云图可视化系统

## Hardware

| Item | Specification |
|------|--------------|
| Recorder | Hongrun T9110 Paperless Recorder (虹润 T9110 无纸记录仪) |
| Sensor | K-type Thermocouple (K型热电偶) |
| Interface | RS485 Serial Port |

## Configuration

### Variable Configuration

These parameters can be modified in the application interface:

| Parameter | Default | Description |
|-----------|---------|-------------|
| Baud Rate | 9600 | Serial communication baud rate |
| Communication Address | 1 | Modbus slave device address |
| Serial Port | COM3 | Windows COM port name |

### Fixed Configuration

| Parameter | Value |
|-----------|-------|
| Protocol | Modbus RTU |
| Function Code | 04 (Read Input Registers) |
| Data Format | 32-bit IEEE 754 Float, word-swapped (CD AB) |
| Data Bits | 8 |
| Stop Bits | 1 |
| Parity | None |

### Channel Register Mapping

The real-time measurement value of **Channel n** is read from **Input Register 2(n-1)** (Function Code 04, 2 registers per channel).

| Channel | Start Register | Hex Address |
|---------|---------------|-------------|
| CH1 | 2(1-1) = 0 | 0x0000 |
| CH2 | 2(2-1) = 2 | 0x0002 |
| CH3 | 2(3-1) = 4 | 0x0004 |
| CH4 | 2(4-1) = 6 | 0x0006 |
| ... | 2(n-1) | ... |
| CH18 | 2(18-1) = 34 | 0x0022 |

Each channel occupies 2 consecutive 16-bit registers. The data is decoded as a 32-bit IEEE 754 floating-point number with **word swap**: high word at register offset+1, low word at register offset+0.

## Components

### t9110_cloudmap.py - Fire-Flood Thermo-Kinetics Evaluation System (Main App)

Window title: 高温火驱原油着火热动力学特征评价系统.

Single full-screen dashboard. Every functional area is laid out directly on
one page (no navigation pages, no popups). The window opens maximized so all
areas are visible at once. The Modbus acquisition core (word-swapped float
decode) is unchanged from the original program; only the visualization /
layout was redesigned. The temperature cloud is interpolated with ordinary
Kriging (`_kriging_1d`).

**Layout:**
- **Left - parameters and probe config:** communication config (port / baud /
  address / refresh interval, edited inline at the top); Basic Parameters
  (live, read only: pipe-wall temperature T8, injection pressure P1,
  production pressure P2); Probe Config (assign each probe to a Modbus channel
  and toggle it on/off); Other Parameters (operator-entered: core porosity /
  permeability / reserved, with a Modify button).
- **Right, top - cloud map:** the device drawn as a single large rectangle.
  The 7 temperature probes sit in one middle row; thick solid lines mark the
  inlet (left) and outlet (right); P1/P2 sit at the ends; the single pipe-wall
  temperature is shown on top. The empty frame is shown from startup; **Start
  Render** fills the Kriging colour cloud. Each probe temperature fills its
  whole vertical column (column-constant), interpolated along the pipe length
  between probes. Start Render / Stop Render buttons are below it.
- **Right, bottom - personalized display:** single-select channel buttons in
  one row (only one channel's value-vs-time curve at a time), plus an Export
  button.

**Channel mapping (default; configurable in Probe Config):**

| Display | Default channel | Meaning |
|---------|-----------------|---------|
| T1 - T7 | CH1 - CH7 | Internal pipe temperatures (Kriging cloud, one middle row) |
| T8      | CH8       | Pipe-wall temperature (single) |
| P1      | CH10      | Injection / inlet pressure |
| P2      | CH11      | Production / outlet pressure |

**Usage:**
1. Adjust communication parameters at the top right if needed (defaults
   COM3 / 9600 / address 1).
2. In Probe Config, set which Modbus channel each probe reads and enable /
   disable individual probes as needed.
3. Click **Start Render**. The button greys out; only **Stop Render** can
   re-enable it. The personalized chart is cleared at the start of each
   render session.
4. The cloud map and per-channel curves update in real time. A disabled
   temperature probe (or one that fails to read) is filled in by Kriging from
   its neighbours. An error popup appears only when every enabled probe fails
   to read (e.g. wrong comm config).
5. Click **Export** to save all channels' real-time data to an Excel file,
   one sub-sheet per channel.
6. Click **Stop Render** to pause real-time updates.

```bash
python t9110_cloudmap.py
```

### t9110_reader.py - CLI Data Reader

Command-line real-time data reader for Channel 1.

```bash
python t9110_reader.py
```

### t9110_diag.py - Diagnostic Tool

Reads raw register values across multiple addresses and function codes to help diagnose communication issues.

```bash
python t9110_diag.py
```

## Installation

### Requirements

- Python 3.8+

```bash
pip install pymodbus pyserial numpy matplotlib scipy openpyxl
```

### Build EXE

Run the provided build script:

```bash
build.bat
```

Or manually:

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name T9110_CloudMap --icon=NONE t9110_cloudmap.py
```

The executable will be generated in the `dist/` folder.

## License

MIT
