# ws_client.py
# แก้: ws.open (deprecated websockets v14+) → self._connected flag
# แก้: reconnect loop ใน connect()

import asyncio
import threading
import websockets
import json
import time


class WSClient:
    def __init__(self, uri: str = None, send_interval: float = 0.05, min_step: int = 1):
        # ถ้าไม่ส่ง uri ให้ดึงจาก config
        if uri is None:
            from config import WS_URL
            uri = WS_URL
        self.uri           = uri
        self.send_interval = send_interval
        self.min_step      = min_step

        self._ws           = None
        self._connected    = False          # ← แทน ws.open ที่ deprecated
        self._loop         = None
        self._thread       = None

        # rate-limit
        self._last_sent      : dict  = {}
        self._last_send_time : float = 0.0
        self._pending        : dict | None = None

        self.start()

    # ── lifecycle ──────────────────────────────────────────────────────────────
    def start(self):
        def _run():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(self._connect_loop())

        self._thread = threading.Thread(target=_run, daemon=True, name="ws-tx")
        self._thread.start()

    async def _connect_loop(self):
        while True:
            try:
                async with websockets.connect(self.uri) as ws:
                    self._ws        = ws
                    self._connected = True
                    print(f"[WS-TX] connected: {self.uri}")
                    async for _ in ws:       # drain inbound (ไม่ใช้)
                        pass
            except Exception as e:
                print(f"[WS-TX] error: {e} — retry 3s")
            finally:
                self._connected = False
                self._ws        = None
            await asyncio.sleep(3)

    # ── send (thread-safe, rate-limited) ──────────────────────────────────────
    def send(self, data: dict):
        now = time.time()
        if now - self._last_send_time < self.send_interval:
            self._pending = data
            return
        self._send_now(data)
        self._last_send_time = now
        if self._pending:
            pending, self._pending = self._pending, None
            threading.Timer(self.send_interval, lambda: self.send(pending)).start()

    def _send_now(self, data: dict):
        cmd = data.get("command")
        val = data.get("value")

        # min_step filter สำหรับ pan/tilt
        if cmd in ("pan", "tilt") and val is not None:
            if abs(val - self._last_sent.get(cmd, val + self.min_step + 1)) < self.min_step:
                return
        if cmd:
            self._last_sent[cmd] = val

        if self._connected and self._ws and self._loop:
            asyncio.run_coroutine_threadsafe(
                self._ws.send(json.dumps(data)), self._loop
            )
