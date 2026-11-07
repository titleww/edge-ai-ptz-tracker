# pantilt_setup.py
import serial

def clamp(value, min_value, max_value):
    return max(min_value, min(value, max_value))

class PanTiltController:
    def __init__(self, port, address):
        self.ser = serial.Serial(port, baudrate=9600, timeout=1)
        self.address = address
        self._current_pan = 0.0
        self._current_tilt = 0.0

    def _send_command(self, command):
        checksum = sum(command[1:]) & 0xFF
        self.ser.write(bytearray(command + [checksum]))

    def set_pan(self, angle):
        angle = clamp(angle, 0, 359.99)
        self._current_pan = angle
        pdata = int(angle * 100)
        self._send_command([0xFF, self.address, 0x00, 0x4B, (pdata >> 8) & 0xFF, pdata & 0xFF])

    def set_tilt(self, angle):
        angle = clamp(angle, -5, 40)
        self._current_tilt = angle
        pdata = int((36000 - abs(angle) * 100) if angle >= 0 else abs(angle) * 100)
        self._send_command([0xFF, self.address, 0x00, 0x4D, (pdata >> 8) & 0xFF, pdata & 0xFF])


    # -------------------------
    # Getters
    # -------------------------
    def get_pan(self):
        return self._current_pan

    def get_tilt(self):
        return self._current_tilt


    def stop(self):
        self._send_command([0xFF, self.address, 0x00, 0x00, 0x00, 0x00])

    def close(self):
        self.ser.close()
