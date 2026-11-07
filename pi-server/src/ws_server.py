# ws_server.py — Pi5
# แก้: asyncio.wait() deprecated → asyncio.gather()
# เพิ่ม: throttle ส่ง detection สูงสุด 10Hz (ไม่ flood Mac)

import asyncio
import websockets
import json
import queue
import time

clients: set = set()
command_queue = queue.Queue()

# throttle: ส่ง detection ไป client สูงสุด 10 ครั้ง/วินาที
_last_send_time: float = 0.0
SEND_INTERVAL: float   = 0.1   # วินาที (= 10 Hz)


async def handler(websocket):
    clients.add(websocket)
    print(f"[WS] client connected: {websocket.remote_address}")
    try:
        async for message in websocket:
            try:
                data = json.loads(message)
                print("[WS] ← client:", data)
                command_queue.put(data)
            except json.JSONDecodeError:
                print("[WS] invalid JSON:", message)
    except websockets.ConnectionClosed:
        pass
    finally:
        clients.discard(websocket)
        print(f"[WS] client disconnected")


async def send_detection(data: dict):
    """
    ส่ง JSON ไป clients ทุกตัว
    - แก้ asyncio.wait([...]) → asyncio.gather() (Python 3.11+ compatible)
    - throttle 10 Hz ป้องกัน flood
    """
    global clients, _last_send_time

    if not hasattr(send_detection, "_counter"):
        send_detection._counter = 0
    send_detection._counter += 1
    if send_detection._counter % 10 == 0:
        print(f"[WS-TX] send_detection called, clients count: {len(clients)}")

    if not clients:
        return

    now = time.monotonic()
    if now - _last_send_time < SEND_INTERVAL:
        return                  # skip frame นี้ ยังไม่ถึงเวลา
    _last_send_time = now

    message    = json.dumps(data)
    dead_clients = set()

    # asyncio.gather แทน asyncio.wait เดิม
    results = await asyncio.gather(
        *[client.send(message) for client in clients],
        return_exceptions=True
    )
    for client, result in zip(list(clients), results):
        if isinstance(result, Exception):
            dead_clients.add(client)

    clients -= dead_clients


def start_server(host: str = "0.0.0.0", port: int = 8765):
    """คืน coroutine สำหรับ ws_loop.run_until_complete(start_server())"""
    print(f"[WS] starting ws://{host}:{port}")
    return websockets.serve(handler, host, port)
