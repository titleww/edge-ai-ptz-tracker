# distance.py
# แก้: เปลี่ยน global state (_last_distance/_last_height) → DistanceCalculator class
#       ป้องกัน state รั่วข้ามรอบ และ test ได้ง่ายขึ้น

import math

AREA_REF     = 6000
DISTANCE_REF = 200.0
MAX_AREA     = 750000


class DistanceCalculator:
    def __init__(self):
        self._last_distance : float | None = None
        self._last_height   : float | None = None
        self._last_auto_tilt: float        = 0.0
        self._last_auto_pan : float        = 0.0

    def get(self, data: dict | None = None,
            manual_tilt: float | None = None,
            manual_pan:  float | None = None):
        """
        คืน (distance, height, pan_deg, tilt_deg, cls, conf, obj_id)
        data = {"detections": [...], "pantilt": {"pan": x, "tilt": y}}
        """
        if data is None:
            from receive_client import get_latest_detections
            data = get_latest_detections()

        dets    = data.get("detections", [])
        pantilt = data.get("pantilt", {})

        auto_tilt = pantilt.get("tilt", 0.0)
        auto_pan  = pantilt.get("pan",  0.0)

        if manual_tilt is not None:
            tilt_deg = manual_tilt
        else:
            tilt_deg = auto_tilt
            self._last_auto_tilt = tilt_deg

        if manual_pan is not None:
            pan_deg = manual_pan
        else:
            pan_deg = auto_pan
            self._last_auto_pan = pan_deg

        # เลือก detection ที่ track=True และ area เล็กสุด (ไกลสุด)
        selected_det  = None
        current_area  = None
        for det in dets:
            if det.get("track"):
                x1, y1, x2, y2 = det["bbox"]
                area = (x2 - x1) * (y2 - y1)
                if current_area is None or area < current_area:
                    current_area = area
                    selected_det = det

        if selected_det is None:
            return None, None, pan_deg, tilt_deg, None, None, None

        # คำนวณ distance
        if current_area is None or current_area >= MAX_AREA:
            distance = self._last_distance
        else:
            distance = DISTANCE_REF * (AREA_REF / current_area) ** 0.5
            self._last_distance = distance

        # คำนวณ height
        tilt_rad = math.radians(tilt_deg)
        if distance is not None:
            height = distance * math.tan(tilt_rad)
            self._last_height = height
        else:
            height = self._last_height

        cls    = selected_det.get("class")
        conf   = selected_det.get("conf")
        obj_id = selected_det.get("display_id")

        return distance, height, pan_deg, tilt_deg, cls, conf, obj_id


# singleton สำหรับ backward compat (ui.py / main_client.py เรียกแบบ function เดิม)
_calc = DistanceCalculator()

def get_drone_distance_and_height(data=None, manual_tilt=None, manual_pan=None):
    return _calc.get(data, manual_tilt, manual_pan)
