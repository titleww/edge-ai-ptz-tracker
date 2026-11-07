# config.py - Client Configuration
import os

PI_IP    = os.getenv("TRACKER_PI_IP", "192.168.1.100")
RTSP_URL = os.getenv("TRACKER_RTSP_URL", f"rtsp://{PI_IP}:8554/stream")
WS_URL   = os.getenv("TRACKER_WS_URL", f"ws://{PI_IP}:8765")
