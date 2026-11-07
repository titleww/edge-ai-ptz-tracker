# receive_client.py
# แก้: เพิ่ม reconnect loop — เดิมหลุดครั้งเดียวแล้วจบ ทำให้ latest_detections = [] ตลอด

import asyncio
import websockets
import json
import threading
from config import WS_URL

_latest: dict = {}          # เก็บเป็น dict (detections + pantilt)
_lock = threading.Lock()


async def _ws_listener():
    """reconnect อัตโนมัติทุก 2 วิถ้าหลุด"""
    while True:
        try:
            async with websockets.connect(WS_URL) as ws:
                print(f"[WS-RX] connected: {WS_URL}")
                async for message in ws:
                    try:
                        data = json.loads(message)
                        if not hasattr(_ws_listener, "_counter"):
                            _ws_listener._counter = 0
                        _ws_listener._counter += 1
                        if _ws_listener._counter % 10 == 0:
                            print(f"[WS-RX] Received telemetry: {data.get('pantilt')}")
                        with _lock:
                            global _latest
                            _latest = data
                    except json.JSONDecodeError as e:
                        print(f"[WS-RX] JSON error: {e}")
        except Exception as e:
            print(f"[WS-RX] disconnected: {e} — retry in 2s")
            await asyncio.sleep(2)          # ← reconnect delay


def _start_loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(_ws_listener())


def run_ws_listener_in_thread():
    t = threading.Thread(target=_start_loop, daemon=True, name="ws-rx")
    t.start()


def get_latest_detections() -> dict:
    """คืน dict {"detections": [...], "pantilt": {...}} thread-safe"""
    with _lock:
        return dict(_latest)
