# select_target.py
import cv2

class TargetSelector:
    def __init__(self, width, height, ws_client=None):
        self.width = width
        self.height = height
        self.ws_client = ws_client
        self.selected_id = None
        self.detections = []

    def update_detections(self, detections):
        # server ส่ง detections มาเป็น dict {"detections": [...]}
        if isinstance(detections, dict) and "detections" in detections:
            self.detections = detections["detections"]
        elif isinstance(detections, list):
            self.detections = detections
        else:
            self.detections = []

        # ถ้า target ที่เลือกหายไป → รีเซ็ต selected_id
        if self.selected_id is not None:
            ids = [det.get("display_id") for det in self.detections if "display_id" in det]
            if self.selected_id not in ids:
                print(f"[TargetSelector] Target {self.selected_id} lost. Deselecting.")
                self.selected_id = None

    def mouse_callback(self, event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN:
            for det in self.detections:
                x1, y1, x2, y2 = det["bbox"]
                display_id = det.get("display_id")
                if x1 <= x <= x2 and y1 <= y <= y2:
                    self.selected_id = display_id
                    print(f"[TargetSelector] Selected drone display_id={display_id}")
                    if self.ws_client:
                        self.ws_client.send({"command": "select_target", "value": display_id})
                    break
