# manual_manager.py
import queue
import time
import math
from pantilt_setup import clamp


class ManualManager:
    def __init__(self, pantilt, command_queue, yolo_manager=None):
        self.pantilt = pantilt
        self.queue = command_queue
        self.yolo = yolo_manager

        self.manual_mode = False
        self.scan_in_progress = False
        self.scan_steps = []
        self.scan_index = 0
        self.scan_delay_start = None

        self.scan_step_deg = 10

        self.current_pan = 0.0
        self.current_tilt = 0.0

        self.frame_width = 640
        self.frame_height = 480

    def update(self, frame=None):
        # self._process_queue()

        # Manual/Scan pan-tilt
        if self.scan_in_progress:
            self._process_scan()
        else:
            if self.pantilt:
                self.pantilt.set_pan(self.current_pan)
                self.pantilt.set_tilt(self.current_tilt)

        # Draw detections asynchronously using the background YOLO detections
        if self.yolo and frame is not None:
            frame = self.yolo.draw_only(frame)

        return frame

    def _process_queue(self):
        while True:
            try:
                data = self.queue.get_nowait()
            except queue.Empty:
                break

            # --- Manual mode on/off ---
            if 'mode' in data and data['mode'] == 'manual':
                was_manual = self.manual_mode
                self.manual_mode = data.get('state', False)
                print(f"[ManualManager] Manual mode set to {self.manual_mode}")

                if self.manual_mode and not was_manual:
                    # เก็บตำแหน่งปัจจุบันเมื่อเปิด manual
                    self.current_pan = self.pantilt.get_pan() if self.pantilt else 0.0
                    self.current_tilt = self.pantilt.get_tilt() if self.pantilt else 0.0

                if not self.manual_mode:
                    # ปิด manual → ค้างตำแหน่งสุดท้าย
                    self.scan_in_progress = False

                    if self.pantilt:
                        self.pantilt.set_pan(self.current_pan)
                        self.pantilt.set_tilt(self.current_tilt)

                    if self.yolo:
                        # บันทึกตำแหน่งล่าสุดของ manual mode
                        self.yolo.start_pan = self.current_pan
                        self.yolo.start_tilt = self.current_tilt
                        # อัปเดต last_pan/last_tilt ด้วยเลย
                        self.yolo.last_pan = self.current_pan
                        self.yolo.last_tilt = self.current_tilt

                    print(f"[ManualManager] Manual mode off. Pan/Tilt kept at {self.current_pan:.2f}, {self.current_tilt:.2f}")

            # --- Start scan (manual only) ---
            elif data.get('command') == 'scan' and self.manual_mode:
                if not self.scan_in_progress:
                    self._start_scan()
                else:
                    print("[ManualManager] Scan already in progress, ignoring new scan command")

            # --- Manual pan/tilt commands ---
            elif self.manual_mode and 'command' in data and 'value' in data:
                cmd = data['command']
                val_raw = data['value']

                # ตรวจสอบกรณีไม่มีค่า (None)
                if val_raw is None:
                    print(f"[WARN] Ignoring {cmd} command with None value")
                    continue

                # แปลงค่าเป็น float อย่างปลอดภัย
                try:
                    val = float(val_raw)
                except (TypeError, ValueError):
                    print(f"[ERROR] Invalid value for {cmd}: {val_raw}")
                    continue

                # อัปเดต pan/tilt ตามคำสั่ง
                if cmd == 'pan':
                    self.current_pan = clamp(val, 0, 359.99)
                    print(f"[ManualManager] Pan updated to {self.current_pan:.2f}")
                elif cmd == 'tilt':
                    self.current_tilt = clamp(val, -5, 40)
                    print(f"[ManualManager] Tilt updated to {self.current_tilt:.2f}")

                # หากกำลัง scan อยู่ → ยกเลิก scan
                if self.scan_in_progress:
                    self.scan_in_progress = False
                    print("[ManualManager] Scan cancelled due to manual pan/tilt")

            # --- Select target command ---
            elif data.get('command') == 'select_target':
                target_id = data.get('value')

                if target_id is not None:
                    if self.yolo is not None:
                        self.yolo.selected_id = target_id
                    print(f"[ManualManager] Selected target_id -> {target_id}")
                else:
                    # หาก value = None ให้ถือว่าเป็นการยกเลิกการเลือกเป้าหมาย
                    if self.yolo is not None:
                        self.yolo.selected_id = None
                    print("[INFO] Target deselected (no target for 5 seconds)")



    def _start_scan(self):
        self.scan_in_progress = True
        self.scan_delay_start = None
        self.scan_start_pan = self.pantilt.get_pan() if self.pantilt else 0.0
        self.scan_accumulated_pan = 0.0
        self.current_pan = self.scan_start_pan
        self.current_tilt = -5.0  # Start at -5 tilt
        self.tilt_max_limit = 40.0 # Maximum tilt limit of the camera
        self.tilt_increment = 5.0  # Increment by +5 degrees
        self.pan_speed = 0.5       # Pan speed (degrees per frame update)

        if self.pantilt:
            self.pantilt.set_pan(self.current_pan)
            self.pantilt.set_tilt(self.current_tilt)

    def _process_scan(self):
        if not self.scan_in_progress:
            return

        # If YOLO detects objects in background, stop scan and hand over to AI tracking
        if self.yolo and hasattr(self.yolo, 'last_detection') and self.yolo.last_detection:
            self.scan_in_progress = False
            print(f"Object detected -> scan stopped at pan={self.current_pan:.2f}, tilt={self.current_tilt:.2f}")
            return

        # Increment accumulated pan
        self.scan_accumulated_pan += self.pan_speed
        
        # Check if we have completed a full 360-degree rotation
        if self.scan_accumulated_pan >= 360.0:
            next_tilt = self.current_tilt + self.tilt_increment
            if next_tilt > self.tilt_max_limit:
                # Reached the maximum limit, stop the scan
                self.scan_in_progress = False
                self.current_pan = 0.0
                self.current_tilt = 0.0
                if self.pantilt:
                    self.pantilt.set_pan(self.current_pan)
                    self.pantilt.set_tilt(self.current_tilt)
                print("Scan finished (reached maximum tilt limit). Pan/Tilt reset to 0.")
                return
            else:
                # Move to next tilt row
                self.current_tilt = next_tilt
                self.scan_accumulated_pan = 0.0
                print(f"[ManualManager] Scan row completed. Stepping tilt to {self.current_tilt:.2f}")

        # Calculate absolute pan angle bounded to [0, 360)
        self.current_pan = (self.scan_start_pan + self.scan_accumulated_pan) % 360

        # Update pantilt hardware
        if self.pantilt:
            self.pantilt.set_pan(self.current_pan)
            self.pantilt.set_tilt(self.current_tilt)
