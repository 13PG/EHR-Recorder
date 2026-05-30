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

### t9110_cloudmap.py - Cloud Map Visualization (Main Application)

GUI application with interactive temperature cloud map.

**Usage:**
1. Set communication parameters (port, baud rate, address)
2. Select cloud map scale (X x Y)
3. Click **Create Grid** to generate grid points
4. Click on grid cells to assign channel numbers (1-18)
5. Click **Show Cloud Map** to display temperature distribution
6. Click **Start Auto-Refresh** for real-time continuous updates

You do not need to assign all grid points. Only assigned channels are used for interpolation:
- **1 channel**: uniform color map at that temperature
- **2 channels**: linear gradient between the two points
- **3+ channels**: full cubic interpolation contour map

Unassigned grid points are excluded from the calculation and shown as gray `+` markers.

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
pip install pymodbus pyserial numpy matplotlib scipy
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
