# main.py — Pi5 (camera + RTSP + WebSocket + YOLO model tracking)
import os
import sys
import time
import threading
import asyncio
import socket
import yaml
from gi.repository import GLib

try:
    from camera_setup   import CameraSetup
    from rtsp_stream    import CameraFactory, RTSPServerManager
    from ws_server      import start_server, command_queue
    from manual_manager import ManualManager
    from uav_detection  import YOLOManager
except ImportError as e:
    print(f"[ERROR] Failed to import modules: {e}")
    print("Please ensure you are running this script from the 'pi-server/tests' directory.")
    sys.exit(1)

# ── Command Line Argument Action Handler ──────────────────────────────────────
if len(sys.argv) > 1:
    action = sys.argv[1].lower()
    pid_file = "main.pid"
    
    if action == "stop":
        import signal
        if os.path.exists(pid_file):
            with open(pid_file, "r") as f:
                try:
                    pid = int(f.read().strip())
                except ValueError:
                    pid = None
            if pid:
                try:
                    os.kill(pid, signal.SIGTERM)
                    print(f"[Main] Stopped main.py (PID {pid})")
                except OSError:
                    print(f"[Main] Process (PID {pid}) not found or already stopped.")
            try:
                os.remove(pid_file)
            except OSError:
                pass
        else:
            print("[Main] main.py is not running (no main.pid found).")
        sys.exit(0)
        
    elif action == "start":
        import subprocess
        if os.path.exists(pid_file):
            # Verify if process is actually running
            with open(pid_file, "r") as f:
                try:
                    pid = int(f.read().strip())
                except ValueError:
                    pid = None
            if pid and os.path.exists(f"/proc/{pid}"):
                print(f"[Main] main.py is already running (PID {pid}).")
                sys.exit(1)
        # Spawn main.py in background with no args
        log_f = open("server.log", "a")
        p = subprocess.Popen(
            [sys.executable, sys.argv[0]],
            stdout=log_f,
            stderr=subprocess.STDOUT,
            close_fds=True
        )
        with open(pid_file, "w") as f:
            f.write(str(p.pid))
        print(f"[Main] Started main.py in background (PID {p.pid}). Logs are in server.log")
        sys.exit(0)
        
    elif action == "restart":
        import subprocess
        subprocess.run([sys.executable, sys.argv[0], "stop"])
        time.sleep(1)
        subprocess.run([sys.executable, sys.argv[0], "start"])
        sys.exit(0)
        
    elif action == "status":
        if os.path.exists(pid_file):
            with open(pid_file, "r") as f:
                pid = f.read().strip()
            if os.path.exists(f"/proc/{pid}"):
                print(f"[Main] main.py is RUNNING (PID {pid})")
            else:
                print(f"[Main] main.pid exists (PID {pid}) but process is DEAD.")
        else:
            print("[Main] main.py is STOPPED.")
        sys.exit(0)
        
    else:
        print("Unknown action. Use: start | stop | restart | status")
        sys.exit(1)

# Write PID file if running the main program
pid_file = "main.pid"
with open(pid_file, "w") as f:
    f.write(str(os.getpid()))

# ── PanTilt Controller Setup ──────────────────────────────────────────────────
pantilt = None
try:
    from pantilt_setup import PanTiltController
    # Try typical Pi serial port
    pantilt = PanTiltController(port="/dev/ttyUSB0", address=1)
    pantilt.set_pan(0.0)
    pantilt.set_tilt(0.0)
    print("[Main] PanTilt Controller initialized on /dev/ttyUSB0")
except Exception as e:
    print(f"[Main] PanTilt Controller not available on /dev/ttyUSB0: {e}")
    print("[Main] System will run in detection-only mode (no motor movements)")

# ── Config Constants ──────────────────────────────────────────────────────────
# Load config.yaml if available
config = {}
config_path = "config.yaml"
for p in ["config.yaml", "../config.yaml", "./tests/config.yaml", "tests/config.yaml", "/home/pi/edge-ai-ptz-tracker/pi-server/config.yaml"]:
    if os.path.exists(p):
        config_path = p
        break
try:
    with open(config_path, "r") as f:
        config = yaml.safe_load(f) or {}
    print(f"[Main] Loaded configuration from {config_path}")
except Exception as e:
    print(f"[Main] [WARN] Failed to load config.yaml: {e}. Using defaults.")

camera_cfg = config.get("camera", {})
WIDTH      = camera_cfg.get("width", 1280)
HEIGHT     = camera_cfg.get("height", 720)
FPS        = camera_cfg.get("fps", 30)
FORMAT     = camera_cfg.get("format", "BGR888")
SHARPNESS  = camera_cfg.get("sharpness", 3.0)
AF_MODE    = camera_cfg.get("af_mode", None)
LENS_POSITION = camera_cfg.get("lens_position", None)

stopping = False

# ── Camera Setup ──────────────────────────────────────────────────────────────
print("[Main] Initializing Camera Setup...")
cam = CameraSetup(
    width=WIDTH, 
    height=HEIGHT, 
    fps=FPS, 
    format=FORMAT, 
    sharpness=SHARPNESS,
    af_mode=AF_MODE,
    lens_position=LENS_POSITION
)
cam.start()

# ── WebSocket Server Setup (asyncio Loop in Background Thread) ────────────────
ws_loop = asyncio.new_event_loop()

async def _start_ws():
    srv = await start_server(host="0.0.0.0", port=8765)
    print("[Main] WebSocket server listening on ws://0.0.0.0:8765")
    return srv

ws_server = None

def _run_ws_loop():
    global ws_server
    asyncio.set_event_loop(ws_loop)
    ws_server = ws_loop.run_until_complete(_start_ws())
    ws_loop.run_forever()

threading.Thread(target=_run_ws_loop, daemon=True, name="ws-loop").start()
time.sleep(0.5)  # Wait for WebSocket server thread to start

# ── HEF Model Path Resolution ─────────────────────────────────────────────────
model_names = ["drone_yolov8s.hef", "drone_v8_nano.hef"]
selected_model_path = None
for name in model_names:
    paths = [
        f"model/{name}",
        f"../model/{name}",
        f"./tests/model/{name}",
        f"tests/model/{name}",
        f"/home/pi/edge-ai-ptz-tracker/pi-server/models/{name}"
    ]
    for p in paths:
        if os.path.exists(p):
            selected_model_path = p
            break
    if selected_model_path:
        break

if not selected_model_path:
    print(f"[Main] [ERROR] Could not locate any model HEF file in: {model_names}")
    cam.stop()
    sys.exit(1)

model_path_abs = os.path.abspath(selected_model_path)
print(f"[Main] Found model at: {selected_model_path}")
print(f"[Main] Resolved absolute path: {model_path_abs}")

# ── YOLO NPU Manager Setup ────────────────────────────────────────────────────
try:
    tracking_cfg = config.get("tracking", {})
    pan_direction = tracking_cfg.get("pan_direction", -1)
    tilt_direction = tracking_cfg.get("tilt_direction", -1)
    class_names = tracking_cfg.get("class_names", None)
    print(f"[Main] Tracking directions -> Pan: {pan_direction}, Tilt: {tilt_direction}")
    if class_names:
        print(f"[Main] Custom Class Names loaded: {class_names}")

    yolo = YOLOManager(
        model_path=model_path_abs,
        target_classes=None,  # Detect all classes dynamically
        ws_loop=ws_loop,
        pan_direction=pan_direction,
        tilt_direction=tilt_direction,
        class_names=class_names
    )
except Exception as e:
    print(f"[Main] [ERROR] Failed to load YOLO Model: {e}")
    cam.stop()
    sys.exit(1)

# ── RTSP Server Setup ─────────────────────────────────────────────────────────
print("[Main] Starting RTSP Server...")
factory     = CameraFactory(WIDTH, HEIGHT, FPS)
rtsp_server = RTSPServerManager(factory, port=8554, mount_point="/stream")
print("[Main] RTSP stream available at rtsp://<Pi-IP>:8554/stream")

# ── Manual Manager Setup ──────────────────────────────────────────────────────
manual_mgr = ManualManager(pantilt=pantilt, command_queue=command_queue, yolo_manager=yolo)
yolo.manual_mgr = manual_mgr

# ── YOLO Background Worker Thread ─────────────────────────────────────────────
# Decouple heavy NPU inference from 60 FPS RTSP encoding loop
def yolo_worker():
    global stopping
    while not stopping:
        try:
            frame = cam.get_frame()
            if frame is None:
                time.sleep(0.01)
                continue

            # In manual or scan mode, run detect_and_draw with allow_tracking=False
            # In tracking mode, allow_tracking=True
            allow_track = not (manual_mgr.manual_mode or manual_mgr.scan_in_progress)
            
            # Run synchronous detection on Hailo NPU in this background thread
            _ = yolo.detect_and_draw(frame, pantilt=pantilt, allow_tracking=allow_track)
        except Exception as e:
            print(f"\n[YOLO Worker Error] Exception caught in background worker thread: {e}")

        # Sleep slightly to prevent tight CPU looping
        time.sleep(0.01)

threading.Thread(target=yolo_worker, daemon=True, name="yolo-worker").start()

# ── GLib Frame Processing Loop ────────────────────────────────────────────────
glib_loop = GLib.MainLoop()

def update_frame() -> bool:
    global stopping
    if stopping:
        return False

    frame = cam.get_frame()
    if frame is None:
        return True

    # Process pending WS control commands
    manual_mgr._process_queue()

    # Determine mode and perform detection/tracking
    if yolo:
        if manual_mgr.manual_mode or manual_mgr.scan_in_progress:
            # Manual / Auto-Scan mode (tracking is managed by ManualManager)
            frame = manual_mgr.update(frame)
        else:
            # Autonomous AI Tracking mode (draw detections asynchronously)
            frame = yolo.draw_only(frame)



    # Push processed frame (with bounding boxes) to RTSP H.264 encoder stream
    factory.push_frame(frame)
    return True

# Run update_frame at target frame rate (16.6ms for 60 FPS)
GLib.timeout_add(int(1000 / FPS), update_frame)

# ── Running and Shutdown Handler ──────────────────────────────────────────────
print("\n=======================================================")
# Check network IPs for user convenience
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(("8.8.8.8", 80))
    local_ip = s.getsockname()[0]
    s.close()
    print(f"  RTSP URL:      rtsp://{local_ip}:8554/stream")
    print(f"  WebSocket URL:  ws://{local_ip}:8765")
except:
    print("  RTSP URL:      rtsp://<Pi-IP>:8554/stream")
    print("  WebSocket URL:  ws://<Pi-IP>:8765")
print("=======================================================\n")
print("[Main] Pipeline fully running... Press Ctrl+C in terminal to stop.")

try:
    glib_loop.run()
except KeyboardInterrupt:
    print("\n[Main] Shutting down pipeline...")
finally:
    stopping = True
    if pantilt:
        print("[Main] Resetting PTZ base position...")
        pantilt.set_pan(0.0)
        pantilt.set_tilt(0.0)
        time.sleep(1.2)
        pantilt.stop()
        pantilt.close()
    print("[Main] Stopping camera...")
    cam.stop()
    if ws_server:
        print("[Main] Closing WebSocket server...")
        ws_server.close()
    ws_loop.call_soon_threadsafe(ws_loop.stop)
    if os.path.exists("main.pid"):
        try:
            os.remove("main.pid")
        except OSError:
            pass
    print("[Main] Shutdown cleanup complete.")