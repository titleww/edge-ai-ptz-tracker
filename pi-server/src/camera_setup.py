# camera_setup.py — Pi5 + Pi Camera v2 (picamera2 thread loop)

import threading
import time
import cv2
import numpy as np
from picamera2 import Picamera2


class CameraSetup:
    def __init__(self, width: int = 1280, height: int = 720, fps: int = 30,
                 format: str = "RGB888", sharpness: float = 4.0,
                 af_mode: int | None = None, lens_position: float | None = None):
        self.width      = width
        self.height     = height
        self.fps        = fps
        self.format     = format
        self.sharpness  = sharpness
        self.af_mode    = af_mode
        self.lens_position = lens_position
        self.fps_actual = 0.0

        self._cam     = None
        self._frame   = None
        self._lock    = threading.Lock()
        self._running = False
        self._thread  = None
        self._fc      = 0
        self._t0      = 0.0

    def start(self):
        self._cam = Picamera2()
        cfg = self._cam.create_video_configuration(
            main={"size": (self.width, self.height), "format": self.format},
            controls={"FrameRate": self.fps},
        )
        self._cam.configure(cfg)
        self._cam.start()

        # Apply camera controls one by one so supported ones succeed even if others fail
        for key, val in [("Sharpness", self.sharpness), ("AfMode", self.af_mode), ("LensPosition", self.lens_position)]:
            if val is not None:
                try:
                    self._cam.set_controls({key: val})
                    print(f"[Camera] Set control {key} = {val} successfully.")
                except Exception as e:
                    print(f"[Camera] Note: Control {key} not supported or failed to set: {e}")

        time.sleep(0.5)

        self._running = True
        self._t0      = time.time()
        self._thread  = threading.Thread(target=self._loop, daemon=True, name="cam-loop")
        self._thread.start()
        print(f"[Camera] started  {self.width}x{self.height} @ {self.fps}fps, format {self.format}")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        if self._cam:
            self._cam.stop()
        print("[Camera] stopped")

    def get_frame(self) -> np.ndarray | None:
        with self._lock:
            return self._frame.copy() if self._frame is not None else None

    def open_camera(self):
        self.start()
        return self

    def _loop(self):
        while self._running:
            try:
                bgr = self._cam.capture_array()  # BGR888 โดยตรง ไม่ต้องแปลงสี

                # Rotate frame 180 degrees because the camera is mounted upside down
                if bgr is not None:
                    bgr = cv2.rotate(bgr, cv2.ROTATE_180)

                with self._lock:
                    self._frame = bgr

                self._fc += 1
                elapsed = time.time() - self._t0
                if elapsed >= 1.0:
                    self.fps_actual = self._fc / elapsed
                    self._fc = 0
                    self._t0 = time.time()

            except Exception as e:
                print(f"[Camera] error: {e}")
                time.sleep(0.05)


_default_cam: CameraSetup | None = None

def open_camera(width=1280, height=720, fps=60) -> CameraSetup:
    global _default_cam
    _default_cam = CameraSetup(width, height, fps)
    _default_cam.start()
    return _default_cam