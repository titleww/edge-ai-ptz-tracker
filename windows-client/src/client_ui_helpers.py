# client_ui_helpers.py
# Helper classes and styling utilities for PTZ Tracking Client UI

import cv2
import time
import threading
from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QLabel, QSizePolicy
from PySide6.QtUiTools import QUiLoader

# ── UI Overlay Class ──────────────────────────────────────────────────────────
class UIOverlay:
    def __init__(self):
        self.last_time   = time.time()
        self.frame_count = 0
        self.fps         = 0
        self.motion_state = "Standby"
        self.last_roll    = 0.0
        self.last_pitch   = 0.0
        self.last_target_det = None

    # ── helper ────────────────────────────────────────────────────────────────
    def rounded_rectangle(self, frame, top_left, bottom_right, color, thickness, radius):
        x1, y1 = top_left
        x2, y2 = bottom_right
        cv2.rectangle(frame, (x1 + radius, y1), (x2 - radius, y2), color, thickness)
        cv2.rectangle(frame, (x1, y1 + radius), (x2, y2 - radius), color, thickness)
        cv2.circle(frame, (x1 + radius, y1 + radius), radius, color, thickness)
        cv2.circle(frame, (x2 - radius, y1 + radius), radius, color, thickness)
        cv2.circle(frame, (x1 + radius, y2 - radius), radius, color, thickness)
        cv2.circle(frame, (x2 - radius, y2 - radius), radius, color, thickness)

    # ── main draw ─────────────────────────────────────────────────────────────
    def draw(self, frame, detections: dict,
             distance: float | None = None,
             height:   float | None = None):
        """
        detections = {"detections": [...], "pantilt": {...}}
        distance, height → คำนวณมาจาก main_client แล้วส่งเข้ามา
        """
        # --- FPS ---
        self.frame_count += 1
        if self.frame_count >= 10:
            now       = time.time()
            self.fps  = 10 / max(now - self.last_time, 1e-6)
            self.last_time   = now
            self.frame_count = 0

        font = cv2.FONT_HERSHEY_COMPLEX
        desired_height = 16
        _, text_h = cv2.getTextSize("FPS: 00", font, 1, 2)[0]
        scale = desired_height / text_h

        # --- Motion state ---
        dets_list = []
        if isinstance(detections, dict):
            dets_list = detections.get("detections", [])
        elif isinstance(detections, list):
            dets_list = detections
        self.motion_state = "Autonomous" if dets_list else "Standby"

        # --- Motion box ---
        longest = "Autonomous"
        motion_w = cv2.getTextSize(f"Motion: {longest}", font, scale, 2)[0][0]
        motion_box_w, motion_box_h = motion_w + 20, 30
        overlay = frame.copy()
        self.rounded_rectangle(overlay, (10, 5), (10 + motion_box_w, 5 + motion_box_h), (60, 60, 60), -1, 10)
        frame = cv2.addWeighted(overlay, 0.8, frame, 0.2, 0)
        cv2.putText(frame, "Motion:", (15, 30), font, scale, (255, 255, 255), 2)
        lw = cv2.getTextSize("Motion:", font, scale, 2)[0][0]
        cv2.putText(frame, self.motion_state, (15 + lw + 5, 30), font, scale, (0, 180, 255), 2)

        # --- FPS box ---
        fps_x = 10 + motion_box_w + 10
        overlay = frame.copy()
        self.rounded_rectangle(overlay, (fps_x, 5), (fps_x + 120, 35), (60, 60, 60), -1, 10)
        frame = cv2.addWeighted(overlay, 0.8, frame, 0.2, 0)
        cv2.putText(frame, "FPS:", (fps_x + 5, 30), font, scale, (255, 255, 255), 2)
        cv2.putText(frame, f"{int(self.fps)}", (fps_x + 65, 30), font, scale, (0, 255, 0), 2)

        # --- Pan / Tilt ---
        if isinstance(detections, dict) and "pantilt" in detections:
            self.last_roll  = detections["pantilt"].get("pan",  self.last_roll)
            self.last_pitch = detections["pantilt"].get("tilt", self.last_pitch)
        pan_val  = 0.0 if round(self.last_roll) >= 360 else self.last_roll
        tilt_val = self.last_pitch

        def _hud_box(label, value_str, color, y_pos):
            lw2 = cv2.getTextSize(label, font, scale, 2)[0][0]
            vw  = cv2.getTextSize(value_str, font, scale, 2)[0][0]
            bw  = lw2 + vw + 25
            ov  = frame.copy()
            self.rounded_rectangle(ov, (10, y_pos), (10 + bw, y_pos + 30), (60, 60, 60), -1, 10)
            cv2.addWeighted(ov, 0.8, frame, 0.2, 0, frame)
            cv2.putText(frame, label,      (20, y_pos + 22), font, scale, (255, 255, 255), 2)
            cv2.putText(frame, value_str,  (20 + lw2 + 5, y_pos + 22), font, scale, color, 2)
            return y_pos + 30 + 10

        y = 5 + motion_box_h + 10
        y = _hud_box("Pan:",      f"{int(round(pan_val))} deg",  (0, 180, 255), y)
        y = _hud_box("Tilt:",     f"{int(round(tilt_val))} deg", (0, 180, 255), y)

        # --- Distance & Height ---
        dist_str   = f"{distance:.1f} m" if distance is not None else "0"
        height_str = f"{height:.1f} m"   if height   is not None else "0"
        y = _hud_box("Distance:", dist_str,   (0, 180, 255), y)
        y = _hud_box("Height:",   height_str, (0, 180, 255), y)

        # --- Crosshair ---
        h, w = frame.shape[:2]
        cx, cy   = w // 2, h // 2
        gap, arm = 20, 60
        col, th  = (0, 255, 255), 2
        cv2.line(frame, (cx - arm, cy), (cx - gap, cy), col, th)
        cv2.line(frame, (cx + gap, cy), (cx + arm, cy), col, th)
        cv2.line(frame, (cx, cy - arm), (cx, cy - gap), col, th)
        cv2.line(frame, (cx, cy + gap), (cx, cy + arm), col, th)

        # --- Zoom view ---
        zoom_w, zoom_h, zm = 200, 150, 10
        target_det = None
        if dets_list:
            # Try to zoom to the actively followed target (track=True) first,
            # otherwise fall back to the first available detection in the list.
            target_det = next((d for d in dets_list if d.get("track")), dets_list[0])

        crop_w, crop_h = 160, 120
        if target_det:
            x1, y1b, x2, y2b = target_det["bbox"]
            tcx = (x1 + x2) // 2
            tcy = (y1b + y2b) // 2
            cls_  = target_det.get("class", "")
            conf_ = target_det.get("conf", 0.0)
            did   = target_det.get("display_id")
            did_str = f" {did}" if did is not None else ""
            label_text = f"{cls_.capitalize()}{did_str}: {int(conf_ * 100)}%"
        else:
            tcx, tcy = w // 2, h // 2
            label_text = "No Target"

        x1_crop = max(0, min(w - crop_w, tcx - crop_w // 2))
        y1_crop = max(0, min(h - crop_h, tcy - crop_h // 2))
        x2_crop = x1_crop + crop_w
        y2_crop = y1_crop + crop_h

        crop = frame[y1_crop:y2_crop, x1_crop:x2_crop].copy()
        if crop.size > 0:
            zoom = cv2.resize(crop, (zoom_w, zoom_h))
            frame[zm:zm + zoom_h, w - zm - zoom_w:w - zm] = zoom
            cv2.rectangle(frame, (w - zm - zoom_w, zm), (w - zm, zm + zoom_h), (255, 255, 255), 2)
            ly1, ly2 = zm + zoom_h, zm + zoom_h + 20
            cv2.rectangle(frame, (w - zm - zoom_w, ly1), (w - zm, ly2), (60, 60, 60), -1)
            (tw, th2), _ = cv2.getTextSize(label_text, font, 0.5, 1)
            cv2.putText(frame, label_text,
                        (w - zm - zoom_w + (zoom_w - tw) // 2, ly1 + (20 + th2) // 2 - 2),
                        font, 0.5, (255, 255, 255), 1)

        return frame

# ── RTSP Thread ───────────────────────────────────────────────────────────────
class RTSPThread(QThread):
    frame_ready = Signal(object)

    def __init__(self, url):
        super().__init__()
        self.url     = url
        self.running = True
        self.busy    = False

    def run(self):
        while self.running:
            cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            if not cap.isOpened():
                time.sleep(2)
                continue
            print("[RTSP] connected")

            while self.running and cap.isOpened():
                ret, frame = cap.read()
                if not ret:
                    break
                if not self.busy:
                    self.busy = True
                    self.frame_ready.emit(frame)

            cap.release()

    def stop(self):
        self.running = False

# ── Video Widget ──────────────────────────────────────────────────────────────
class VideoWidget(QLabel):
    target_selected = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.overlay = UIOverlay()
        self.fw = 1280
        self.fh = 720

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            click_x = event.position().x()
            click_y = event.position().y()

            ww = self.width()
            wh = self.height()
            if ww <= 0 or wh <= 0:
                return

            fw, fh = self.fw, self.fh
            aspect = fw / fh
            if ww / wh > aspect:
                nw = int(wh * aspect)
                nh = wh
            else:
                nw = ww
                nh = int(ww / aspect)

            dx = (ww - nw) / 2.0
            dy = (wh - nh) / 2.0

            rx = click_x - dx
            ry = click_y - dy

            if 0 <= rx <= nw and 0 <= ry <= nh:
                orig_x = int(rx * (fw / nw))
                orig_y = int(ry * (fh / nh))

                from receive_client import get_latest_detections
                det = get_latest_detections()
                dets_list = det.get("detections", []) if isinstance(det, dict) else []

                clicked_id = None
                for d in dets_list:
                    x1, y1, x2, y2 = d["bbox"]
                    if x1 <= orig_x <= x2 and y1 <= orig_y <= y2:
                        clicked_id = d.get("display_id")
                        break

                self.target_selected.emit(clicked_id)

    def set_frame(self, frame, detections=None, distance=None, height=None):
        self.fh, self.fw = frame.shape[:2]
        # Draw overlay HUD, crosshair, and target PIP zoom
        frame = self.overlay.draw(frame, detections, distance, height)

        widget_w = self.width()
        widget_h = self.height()
        if widget_w > 0 and widget_h > 0:
            fh, fw = frame.shape[:2]
            aspect = fw / fh
            if widget_w / widget_h > aspect:
                nw = int(widget_h * aspect)
                nh = widget_h
            else:
                nw = widget_w
                nh = int(widget_w / aspect)

            if nw > 0 and nh > 0:
                frame = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_LINEAR)

        h, w = frame.shape[:2]
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        
        pix = QPixmap.fromImage(
            QImage(rgb.data, w, h, w*3, QImage.Format_RGB888).copy())
        self.setPixmap(pix)

    def resizeEvent(self, e):
        super().resizeEvent(e)


# ── Custom UI Loader ──────────────────────────────────────────────────────────
class CustomUiLoader(QUiLoader):
    def createWidget(self, class_name, parent=None, name=""):
        if name == "video":
            return VideoWidget(parent)
        return super().createWidget(class_name, parent, name)

# ── Styling and Utilities ─────────────────────────────────────────────────────
PTZ_BTN_ON = """
    QPushButton {
        background:#222; color:#bbb;
        border:1px solid #444; border-radius:6px; font-size:15px;
    }
    QPushButton:pressed { background:#334; border-color:#66f; }
"""

PTZ_BTN_OFF = """
    QPushButton {
        background:#1a1a1a; color:#333;
        border:1px solid #2a2a2a; border-radius:6px; font-size:15px;
    }
"""

def style_header_lbl(lbl, text):
    lbl.setText(text)
    lbl.setStyleSheet("color: #ccc; font-size: 11px;")

def style_val_lbl(lbl, text):
    lbl.setText(text)
    lbl.setStyleSheet("color: #fff; font-weight: bold; font-size: 12px;")
