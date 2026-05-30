"""
虹润 T9110 无纸记录仪 - 实时数据采集程序
通过 RS485 (Modbus RTU) 读取通道1实时数据 (K型热电偶)
"""

import sys
import time
import struct
from datetime import datetime

from pymodbus.client import ModbusSerialClient

# ============ 通信参数配置 ============
SERIAL_PORT = "COM3"
BAUD_RATE = 9600
DATA_BITS = 8
STOP_BITS = 1
PARITY = "N"
SLAVE_ID = 1

# ============ 寄存器配置 ============
CH1_REGISTER = 0x0000        # 通道1寄存器地址
REGISTER_COUNT = 2           # 2个寄存器 (32位浮点数)
FUNCTION_CODE = 4            # 功能码4: 输入寄存器

# ============ 采集参数 ============
POLL_INTERVAL = 1.0


def create_client():
    client = ModbusSerialClient(
        port=SERIAL_PORT,
        baudrate=BAUD_RATE,
        bytesize=DATA_BITS,
        stopbits=STOP_BITS,
        parity=PARITY,
        timeout=3,
    )
    return client


def read_channel1(client):
    result = client.read_input_registers(
        address=CH1_REGISTER,
        count=REGISTER_COUNT,
        device_id=SLAVE_ID,
    )
    if result.isError():
        raise Exception(f"Modbus error: {result}")

    r = result.registers
    # 字交换 (CD AB): 高字在reg[1]，低字在reg[0]
    raw = struct.pack('>HH', r[1], r[0])
    return struct.unpack('>f', raw)[0]


def main():
    print("=" * 55)
    print("  虹润 T9110 无纸记录仪 - 实时数据采集")
    print("  传感器: K型热电偶")
    print("=" * 55)
    print(f"  串口: {SERIAL_PORT}  波特率: {BAUD_RATE}")
    print(f"  站号: {SLAVE_ID}  寄存器: 0x{CH1_REGISTER:04X}")
    print(f"  数据格式: 32位浮点 (字交换)  功能码: {FUNCTION_CODE}")
    print(f"  采集间隔: {POLL_INTERVAL}s")
    print("=" * 55)

    client = create_client()

    if not client.connect():
        print(f"\n[错误] 无法连接到串口 {SERIAL_PORT}")
        sys.exit(1)

    print(f"\n[成功] 已连接到 {SERIAL_PORT}")
    print("开始采集数据... (按 Ctrl+C 停止)\n")
    print(f"{'时间':<26} {'温度 (°C)':>12}")
    print("-" * 40)

    read_count = 0
    error_count = 0

    try:
        while True:
            try:
                temp = read_channel1(client)
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

                print(f"{timestamp}  {temp:>10.2f}")

                read_count += 1
                error_count = 0

            except Exception as e:
                error_count += 1
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                print(f"{timestamp}  [通信错误] {e}")

                if error_count >= 5:
                    print("\n[警告] 连续5次错误，尝试重连...")
                    client.close()
                    time.sleep(2)
                    if not client.connect():
                        print("[错误] 重连失败")
                        sys.exit(1)
                    print("[成功] 已重连\n")
                    error_count = 0

            time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        print(f"\n\n采集结束，共读取 {read_count} 次")
    finally:
        client.close()
        print("串口已关闭")


if __name__ == "__main__":
    main()
