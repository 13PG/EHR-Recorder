"""
T9110 diagnostic - read raw register values to determine data format
"""
import struct
from pymodbus.client import ModbusSerialClient

SERIAL_PORT = "COM3"
BAUD_RATE = 9600
SLAVE_ID = 1

client = ModbusSerialClient(
    port=SERIAL_PORT,
    baudrate=BAUD_RATE,
    bytesize=8,
    stopbits=1,
    parity="N",
    timeout=3,
)

if not client.connect():
    print("[ERROR] Cannot connect to", SERIAL_PORT)
    exit(1)

print("Connected to", SERIAL_PORT)
print("=" * 60)

print("\n[1] Read registers 0x0000 - 0x0009 (FC=03 Holding Registers)")
print("-" * 60)
try:
    result = client.read_holding_registers(address=0x0000, count=10, device_id=SLAVE_ID)
    if not result.isError():
        for i, val in enumerate(result.registers):
            signed = val if val < 32768 else val - 65536
            print(f"  Reg {i:04d}: {val:5d} (0x{val:04X})  signed: {signed:6d}  /10={signed/10:.1f}  /100={signed/100:.2f}")
    else:
        print(f"  Error: {result}")
except Exception as e:
    print(f"  Exception: {e}")

print("\n[2] Read registers 0x0000 - 0x0009 (FC=04 Input Registers)")
print("-" * 60)
try:
    result = client.read_input_registers(address=0x0000, count=10, device_id=SLAVE_ID)
    if not result.isError():
        for i, val in enumerate(result.registers):
            signed = val if val < 32768 else val - 65536
            print(f"  Reg {i:04d}: {val:5d} (0x{val:04X})  signed: {signed:6d}  /10={signed/10:.1f}  /100={signed/100:.2f}")
    else:
        print(f"  Error: {result}")
except Exception as e:
    print(f"  Exception: {e}")

print("\n[3] Try float32 decode from reg 0-1 (FC=03)")
print("-" * 60)
try:
    result = client.read_holding_registers(address=0x0000, count=2, device_id=SLAVE_ID)
    if not result.isError():
        r = result.registers
        print(f"  Raw: [{r[0]} (0x{r[0]:04X}), {r[1]} (0x{r[1]:04X})]")
        for name, a, b in [("AB CD (Big Endian)", r[0], r[1]), ("CD AB (Word Swap)", r[1], r[0])]:
            raw = struct.pack('>HH', a, b)
            val = struct.unpack('>f', raw)[0]
            print(f"  {name}: {val}")
    else:
        print(f"  Error: {result}")
except Exception as e:
    print(f"  Exception: {e}")

print("\n[4] Try common alternate addresses (FC=03)")
print("-" * 60)
for addr in [0x0064, 0x03E8, 0x1000, 0x2000, 0x0100]:
    try:
        result = client.read_holding_registers(address=addr, count=2, device_id=SLAVE_ID)
        if not result.isError():
            r = result.registers
            signed0 = r[0] if r[0] < 32768 else r[0] - 65536
            print(f"  Addr 0x{addr:04X}: [{r[0]:5d} (0x{r[0]:04X}), {r[1]:5d} (0x{r[1]:04X})]  reg0/10={signed0/10:.1f}")
        else:
            print(f"  Addr 0x{addr:04X}: Error - {result}")
    except Exception as e:
        print(f"  Addr 0x{addr:04X}: Exception - {e}")

client.close()
print("\n" + "=" * 60)
print("Done. Check which values match your thermocouple reading.")
