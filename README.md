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

### t9110_cloudmap.py - 1D Fire-Flood Simulation Monitor (Main Application)

Single full-screen dashboard. Every functional area is laid out directly on
one page (no navigation pages, no popups). The window opens maximized so all
areas are visible at once. The Modbus acquisition core (word-swapped float
decode) and scipy interpolation are unchanged from the original program; only
the visualization / layout was redesigned.

**Layout:**
- **Left - parameters:** communication config (port / baud / address /
  refresh interval, edited inline at the top), Basic Parameters (live, read
  only: pipe-wall temperatures T8/T9, injection pressure P1, production
  pressure P2), Other Parameters (operator-entered: core porosity /
  permeability / reserved, with a Modify button).
- **Right, top - cloud map:** the 1D fire-flood device drawn directly in the
  figure (pipe body, end flanges, inlet/outlet nozzles, the 7 numbered
  measurement points, and P1/P2 at the ends). The empty device frame is shown
  from startup; **Start Render** fills the interpolated colour cloud inside
  the pipe body and shows each point's value. Start Render / Stop Render
  buttons are below it.
- **Right, bottom - personalized display:** per-channel toggle buttons that
  plot each channel's value-vs-time curve, plus an Export button.

**Fixed channel mapping:**

| Display | Channel | Meaning |
|---------|---------|---------|
| T1 - T7 | CH1 - CH7 | Internal pipe temperatures (interpolated cloud map) |
| T8, T9  | CH8, CH9  | Pipe-wall temperatures (external) |
| P1      | CH10      | Injection / inlet pressure |
| P2      | CH11      | Production / outlet pressure |

**Usage:**
1. Adjust communication parameters at the top right if needed (defaults
   COM3 / 9600 / address 1).
2. Click **Start Render**. The button greys out; only **Stop Render** can
   re-enable it. The personalized chart is cleared at the start of each
   render session.
3. The pipe cloud map and per-channel curves update in real time at the
   configured interval.
4. Missing channels are filled in by interpolation on the cloud map; an error
   popup appears only when **every** channel fails to read (e.g. wrong comm
   config).
5. Click **Export** to save all channels' real-time data to an Excel file,
   one sub-sheet per channel.
6. Click **Stop Render** to pause real-time updates.

The 7 internal temperature points sit at fixed zig-zag positions along the
pipe and are combined by interpolation into a single cloud map; pipe-wall
temperatures and the two pressures are shown outside the pipe.

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
