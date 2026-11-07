# rtsp_stream.py — Pi5

import gi
gi.require_version('Gst', '1.0')
gi.require_version('GstRtspServer', '1.0')
gi.require_version('GstRtsp', '1.0')
from gi.repository import Gst, GstRtspServer, GstRtsp

Gst.init(None)


class CameraFactory(GstRtspServer.RTSPMediaFactory):
    def __init__(self, width: int, height: int, fps: int):
        super().__init__()
        self.set_shared(True)
        self.appsrc    = None
        self.timestamp = 0
        self.width     = width
        self.height    = height
        self.fps       = fps

    def do_create_element(self, url):
        launch_str = (
            f"appsrc name=mysrc is-live=true block=false format=time "
            f"caps=video/x-raw,format=BGR,width={self.width},height={self.height},"
            f"framerate={self.fps}/1 ! "
            f"videoconvert ! video/x-raw,format=I420 ! "
            f"x264enc tune=zerolatency bitrate=4000 speed-preset=veryfast "
            f"key-int-max={self.fps} ! "
            f"rtph264pay name=pay0 pt=96 config-interval=1"
        )
        pipeline = Gst.parse_launch(launch_str)
        self.appsrc = pipeline.get_by_name("mysrc")
        if self.appsrc:
            try:
                # Disable byte limit so that queue size is controlled strictly by number of buffers
                self.appsrc.set_property("max-bytes", 0)
                # 2 is GST_APP_LEAKY_TYPE_DOWNSTREAM (drop oldest buffers when queue is full)
                self.appsrc.set_property("leaky-type", 2)
                # Keep max 100 buffers in queue to allow startup keyframes to survive the handshake
                self.appsrc.set_property("max-buffers", 100)
            except Exception as e:
                print(f"[RTSP] Warning: Could not set queue limits on appsrc: {e}")
        return pipeline

    def do_configure(self, media):
        media.connect("unprepared", self._on_unprepared)

    def _on_unprepared(self, media):
        print("[RTSP] Client disconnected, clearing appsrc.")
        self.appsrc = None

    def push_frame(self, frame):
        if self.appsrc is None:
            return
        try:
            frame  = frame.copy()
            data   = frame.tobytes()
            buf    = Gst.Buffer.new_allocate(None, len(data), None)
            buf.fill(0, data)
            buf.duration    = Gst.util_uint64_scale_int(1, Gst.SECOND, self.fps)
            buf.pts = buf.dts = self.timestamp
            self.timestamp += buf.duration
            self.appsrc.emit("push-buffer", buf)
        except Exception as e:
            print(f"[RTSP Error] Failed to push frame: {e}")
            self.appsrc = None


class RTSPServerManager:
    def __init__(self, factory: CameraFactory,
                 port: int = 8554,
                 mount_point: str = "/stream"):
        self.server = GstRtspServer.RTSPServer()
        self.server.props.service = str(port)
        mounts = self.server.get_mount_points()
        mounts.add_factory(mount_point, factory)
        factory.set_protocols(
            GstRtsp.RTSPLowerTrans.TCP |
            GstRtsp.RTSPLowerTrans.UDP |
            GstRtsp.RTSPLowerTrans.UDP_MCAST
        )
        self.server.attach(None)
        self.factory     = factory
        self.mount_point = mount_point
        self.port        = port
        print(f"[RTSP] server ready  rtsp://<ip>:{port}{mount_point}")
