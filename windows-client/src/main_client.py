# main_client.py — PySide6 v3
# PTZ ใช้งานได้เฉพาะเมื่อ Manual ON

import os
# Force RTSP to use TCP with low delay and no buffer to maximize FPS and minimize latency
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|fflags;nobuffer|flags;low_delay|max_delay;100000"

import sys
import time
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QFile, QTimer, QObject, QEvent

from config         import RTSP_URL, WS_URL
from receive_client import run_ws_listener_in_thread, get_latest_detections
from distance       import get_drone_distance_and_height
from ws_client      import WSClient

# Import UI helpers and thread class
from client_ui_helpers import (
    RTSPThread, CustomUiLoader,
    PTZ_BTN_ON, PTZ_BTN_OFF, style_header_lbl, style_val_lbl
)

# ── Main Window ───────────────────────────────────────────────────────────────
class MainWindow(QObject):
    def __init__(self):
        super().__init__()
        
        # Load UI file from root folder (two levels up from tests directory)
        loader = CustomUiLoader()
        ui_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uxuinewset.ui")
        
        file = QFile(ui_path)
        if not file.open(QFile.ReadOnly):
            print(f"Error: Cannot open UI file at {ui_path}")
            sys.exit(-1)
            
        self.ui = loader.load(file)
        file.close()
        
        # Install event filter to intercept the window close event
        self.ui.installEventFilter(self)
        
        self.ui.setWindowTitle("Optical PTZ Tracking Control Center")
        
        self.ws      = WSClient(uri=WS_URL)
        run_ws_listener_in_thread()

        self._pan_val  = 0.0
        self._tilt_val = 0.0
        self._manual   = False
        self._fps_count= 0
        self._fps_t    = time.time()
        self._fps_val  = 0

        # Map UI Elements
        self.btn_manual = self.ui.pushButton
        self.bu = self.ui.pushButton_2
        self.bd = self.ui.pushButton_7
        self.bl = self.ui.pushButton_9
        self.br = self.ui.pushButton_8
        self.bc = self.ui.pushButton_10
        self.btn_reset = self.ui.pushButton_11
        
        # We need self.video to be referenced by the rtsp loop
        self.video = self.ui.video

        # Style Static Labels
        self.ui.label.setText("Optical PTZ Tracking Control Center")
        self.ui.label.setStyleSheet("color: #fff; font-size: 13px; font-weight: bold; letter-spacing: 2px;")
        self.ui.label.setFixedWidth(180)

        style_header_lbl(self.ui.label_2, "Motion")
        style_header_lbl(self.ui.label_3, "FPS")
        style_header_lbl(self.ui.label_4, "Pan")
        style_header_lbl(self.ui.label_5, "Tilt")
        style_header_lbl(self.ui.label_6, "Distance")
        style_header_lbl(self.ui.label_7, "Altitude")

        self.ui.label_14.setText("MANUAL")
        self.ui.label_14.setStyleSheet("color: #888; font-size: 11px; letter-spacing: 1px;")

        # Set default text for telemetry values
        style_val_lbl(self.ui.label_11, "--") # Motion value
        style_val_lbl(self.ui.label_12, "0")  # FPS value
        style_val_lbl(self.ui.label_10, "0°") # Pan value
        style_val_lbl(self.ui.label_13, "0°") # Tilt value
        style_val_lbl(self.ui.label_8, "--")  # Distance value
        style_val_lbl(self.ui.label_9, "--")  # Altitude value

        # Set Manual ON/OFF styles and state
        self._set_manual_style(False)
        for b in [self.bu, self.bd, self.bl, self.br, self.bc, self.btn_reset]:
            b.setEnabled(False)
            b.setStyleSheet(PTZ_BTN_OFF)

        # Connect Signals
        self.btn_manual.clicked.connect(self._toggle_manual)
        
        # Continuous PTZ movements using a QTimer
        self.ptz_timer = QTimer(self.ui)
        self.ptz_timer.setInterval(50)  # 50ms interval for smooth continuous movement
        self.ptz_timer.timeout.connect(self._on_ptz_timer)
        self.current_ptz_action = None

        self.bu.pressed.connect(lambda: self._start_ptz_action("tilt", 1.0))
        self.bu.released.connect(self._stop_ptz_action)
        self.bd.pressed.connect(lambda: self._start_ptz_action("tilt", -1.0))
        self.bd.released.connect(self._stop_ptz_action)
        self.bl.pressed.connect(lambda: self._start_ptz_action("pan", -1.5))
        self.bl.released.connect(self._stop_ptz_action)
        self.br.pressed.connect(lambda: self._start_ptz_action("pan", 1.5))
        self.br.released.connect(self._stop_ptz_action)

        self.bc.pressed.connect(self._reset)
        self.btn_reset.clicked.connect(self._reset)
        self.video.target_selected.connect(self._select_target_by_id)

        self.rtsp = RTSPThread(RTSP_URL)
        self.rtsp.frame_ready.connect(self.on_frame)
        self.rtsp.start()

        self.timer = QTimer(self.ui)
        self.timer.timeout.connect(self.update_panel)
        self.timer.start(100)

    def on_frame(self, frame):
        self._fps_count += 1
        now = time.time()
        if now - self._fps_t >= 1.0:
            self._fps_val   = self._fps_count
            self._fps_count = 0
            self._fps_t     = now
            
        det = get_latest_detections()
        dist, height, *_ = get_drone_distance_and_height(det)
        self.video.set_frame(frame, det, dist, height)
        self.rtsp.busy = False

    def update_panel(self):
        det = get_latest_detections()
        dets  = det.get("detections",[]) if isinstance(det,dict) else []
        pt    = det.get("pantilt",{})    if isinstance(det,dict) else {}
        pan   = pt.get("pan",0.0)
        tilt  = pt.get("tilt",0.0)
        mot   = "Autonomous" if dets else "Standby"
        mc    = "#ffaa44" if dets else "#55cc55"
        dist, height, *_ = get_drone_distance_and_height(det)
        
        self.ui.label_11.setText(mot)
        self.ui.label_11.setStyleSheet(f"color:{mc}; font-weight:bold; font-size:12px;")
        
        self.ui.label_12.setText(str(self._fps_val))
        self.ui.label_12.setStyleSheet("color:#55cc55; font-weight:bold; font-size:12px;")
        
        self.ui.label_10.setText(f"{int(round(pan))}°")
        self.ui.label_10.setStyleSheet("color:#44ccff; font-weight:bold; font-size:12px;")
        
        self.ui.label_13.setText(f"{int(round(tilt))}°")
        self.ui.label_13.setStyleSheet("color:#44ccff; font-weight:bold; font-size:12px;")
        
        self.ui.label_8.setText(f"{dist:.1f} m" if dist is not None else "--")
        self.ui.label_8.setStyleSheet("color:#fff; font-weight:bold; font-size:12px;")
        
        self.ui.label_9.setText(f"{height:.1f} m" if height is not None else "--")
        self.ui.label_9.setStyleSheet("color:#fff; font-weight:bold; font-size:12px;")

    def _toggle_manual(self):
        self._manual = not self._manual
        self._set_manual_style(self._manual)
        self._stop_ptz_action()

        for b in [self.bu, self.bd, self.bl, self.br, self.bc, self.btn_reset]:
            b.setEnabled(self._manual)
            b.setStyleSheet(PTZ_BTN_ON if self._manual else PTZ_BTN_OFF)

        self.btn_reset.setStyleSheet("""
            QPushButton { background:#1a1a1a; color:%s;
                border:1px solid %s; border-radius:5px;
                padding:5px; font-size:11px; }
            QPushButton:pressed { background:#222; }
        """ % (("#888","#333") if self._manual else ("#333","#2a2a2a")))

        self.ws.send({"mode":"manual","state":self._manual})

    def _start_ptz_action(self, axis, delta):
        if not self._manual:
            return
        self._ptz(axis, delta)
        self.current_ptz_action = (axis, delta)
        self.ptz_timer.start()

    def _stop_ptz_action(self):
        if hasattr(self, "ptz_timer"):
            self.ptz_timer.stop()
        self.current_ptz_action = None

    def _on_ptz_timer(self):
        if self._manual and self.current_ptz_action:
            axis, delta = self.current_ptz_action
            self._ptz(axis, delta)

    def _set_manual_style(self, on):
        if on:
            self.btn_manual.setText("ON")
            self.btn_manual.setStyleSheet("""
                QPushButton { background:#1a3a1a; color:#55cc55;
                    border:1px solid #55cc55; border-radius:5px;
                    font-size:11px; font-weight:bold; }
            """)
        else:
            self.btn_manual.setText("OFF")
            self.btn_manual.setStyleSheet("""
                QPushButton { background:#1e1e1e; color:#555;
                    border:1px solid #333; border-radius:5px; font-size:11px; }
            """)

    def _ptz(self, axis, delta):
        if axis == "pan":
            self._pan_val = (self._pan_val + delta) % 360
            self.ws.send({"command":"pan","value":self._pan_val})
        else:
            self._tilt_val = max(-5, min(40, self._tilt_val + delta))
            self.ws.send({"command":"tilt","value":self._tilt_val})

    def _reset(self):
        self._pan_val = self._tilt_val = 0.0
        self.ws.send({"command":"pan","value":0})
        self.ws.send({"command":"tilt","value":0})

    def _select_target_by_id(self, target_id):
        self.ws.send({"command": "select_target", "value": target_id})


    def eventFilter(self, obj, event):
        if obj == self.ui and event.type() == QEvent.Close:
            # Clean up background threads
            self.rtsp.stop()
            self.rtsp.wait()
        return super().eventFilter(obj, event)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    win.ui.show()
    sys.exit(app.exec())