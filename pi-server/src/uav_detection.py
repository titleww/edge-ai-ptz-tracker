# uav_detection.py — Pi5 Hailo-8L version (fixed)

import cv2
import asyncio
import numpy as np
import time
from ws_server import send_detection
from sort import Sort

from hailo_platform import (
    HEF, VDevice, HailoStreamInterface,
    InputVStreamParams, OutputVStreamParams,
    FormatType, InferVStreams, ConfigureParams
)

STRIDES     = [8, 16, 32]
REG_MAX     = 16
CONF_THRESH = 0.30
IOU_THRESH  = 0.45
INPUT_SIZE  = 640


def _dfl_decode(reg):
    x  = reg - reg.max(axis=-1, keepdims=True)
    ex = np.exp(x)
    sm = ex / ex.sum(axis=-1, keepdims=True)
    return (sm * np.arange(REG_MAX, dtype=np.float32)).sum(axis=-1)


def _make_grid(h, w, stride):
    gy, gx = np.meshgrid(np.arange(h), np.arange(w), indexing="ij")
    return (np.stack([gx, gy], axis=-1).reshape(-1, 2).astype(np.float32) + 0.5) * stride


def decode_outputs(reg_list, cls_list):
    boxes_all, scores_all, class_ids_all = [], [], []
    sizes = [(INPUT_SIZE // s, INPUT_SIZE // s) for s in STRIDES]
    
    global_max_conf = 0.0
    
    for (h, w), stride, reg_flat, cls_flat in zip(sizes, STRIDES, reg_list, cls_list):
        # cls_flat shape is (N, num_classes)
        N    = h * w
        dist = _dfl_decode(reg_flat.reshape(N, 4, REG_MAX))
        grid = _make_grid(h, w, stride)
        
        # Bugfix: Multiply dist by stride to scale grid cell distances to pixel scale
        xyxy = np.stack([
            grid[:,0]-dist[:,0]*stride, grid[:,1]-dist[:,1]*stride,
            grid[:,0]+dist[:,2]*stride, grid[:,1]+dist[:,3]*stride
        ], axis=1)
        
        probs = 1 / (1 + np.exp(-cls_flat)) # (N, num_classes)
        conf = probs.max(axis=-1) # (N,)
        class_ids = probs.argmax(axis=-1) # (N,)
        
        if len(conf) > 0:
            stride_max = float(conf.max())
            if stride_max > global_max_conf:
                global_max_conf = stride_max
                
        mask = conf > CONF_THRESH
        if mask.any():
            boxes_all.append(xyxy[mask])
            scores_all.append(conf[mask])
            class_ids_all.append(class_ids[mask])
            
    if not boxes_all:
        return np.empty((0,6), dtype=np.float32), global_max_conf
        
    boxes  = np.concatenate(boxes_all)
    scores = np.concatenate(scores_all)
    class_ids = np.concatenate(class_ids_all)
    bxywh  = boxes.copy(); bxywh[:,2]-=bxywh[:,0]; bxywh[:,3]-=bxywh[:,1]
    
    idxs   = cv2.dnn.NMSBoxes(bxywh.tolist(), scores.tolist(), CONF_THRESH, IOU_THRESH)
    if len(idxs) == 0:
        return np.empty((0,6), dtype=np.float32), global_max_conf
        
    idxs = np.array(idxs).flatten()
    results = np.concatenate([
        boxes[idxs],
        scores[idxs,None],
        class_ids[idxs,None].astype(np.float32)
    ], axis=1)
    
    return results, global_max_conf


class YOLOManager:
    CLASS_NAMES = ["drone", "uav", "aircraft", "bird", "helicopter", "airplane"]

    def __init__(self, model_path, target_classes=None, ws_loop=None, manual_mgr=None, pan_direction=-1, tilt_direction=-1, class_names=None):
        if class_names:
            self.CLASS_NAMES = class_names
        self.ws_loop        = ws_loop
        self.target_classes = target_classes or self.CLASS_NAMES
        self.manual_mgr     = manual_mgr
        self.pan_direction  = pan_direction
        self.tilt_direction = tilt_direction

        print(f"[YOLO] Loading {model_path} ...")
        self.hef    = HEF(model_path)

        # Print input vstream infos
        print("[YOLO] --- Input VStream Infos ---")
        for info in self.hef.get_input_vstream_infos():
            details = f"  Name: {info.name}, Shape: {getattr(info, 'shape', 'unknown')}"
            if hasattr(info, 'data_type'):
                details += f", Type: {info.data_type}"
            print(details)
            if hasattr(info, 'quant_info') and info.quant_info:
                scale = getattr(info.quant_info, 'qp_scale', 'unknown')
                zp = getattr(info.quant_info, 'qp_zp', 'unknown')
                print(f"    Quant Info -> Scale: {scale}, Zero Point: {zp}")

        # Print output vstream infos
        print("[YOLO] --- Output VStream Infos ---")
        for info in self.hef.get_output_vstream_infos():
            details = f"  Name: {info.name}, Shape: {getattr(info, 'shape', 'unknown')}"
            if hasattr(info, 'data_type'):
                details += f", Type: {info.data_type}"
            print(details)
            if hasattr(info, 'quant_info') and info.quant_info:
                scale = getattr(info.quant_info, 'qp_scale', 'unknown')
                zp = getattr(info.quant_info, 'qp_zp', 'unknown')
                print(f"    Quant Info -> Scale: {scale}, Zero Point: {zp}")

        self.target = VDevice()
        cfg = ConfigureParams.create_from_hef(self.hef, interface=HailoStreamInterface.PCIe)
        self.ng     = self.target.configure(self.hef, cfg)[0]

        info = self.hef.get_input_vstream_infos()[0]
        self.input_h, self.input_w = info.shape[0], info.shape[1]
        self._inp_name = info.name

        # Check if model needs manual normalization (FLOAT32 in [0, 1])
        # If scale is 1.0 (or close), it means the NPU expects raw float32 values.
        # If scale is small (like 0.0039), it expects UINT8.
        self.manual_normalize = True
        if hasattr(info, 'quant_info') and info.quant_info:
            scale = getattr(info.quant_info, 'qp_scale', 1.0)
            if abs(scale - 1.0) > 0.01:
                self.manual_normalize = False
                print(f"[YOLO] NPU has quantization scale ({scale}). Using UINT8 mode.")
            else:
                print("[YOLO] NPU input scale is 1.0. Using FLOAT32 Normalized mode.")
        else:
            print("[YOLO] No quantization scale found. Using FLOAT32 Normalized mode.")

        if self.manual_normalize:
            self.in_params = InputVStreamParams.make(self.ng, format_type=FormatType.FLOAT32)
        else:
            self.in_params = InputVStreamParams.make(self.ng, format_type=FormatType.UINT8)

        self.out_params = OutputVStreamParams.make(self.ng, format_type=FormatType.FLOAT32)
        # Resolve output layer names dynamically from HEF metadata
        out_infos = self.hef.get_output_vstream_infos()
        # Regression layers have shape (H, W, 64)
        reg_infos = sorted([info for info in out_infos if info.shape[2] == 64], key=lambda x: x.shape[0], reverse=True)
        # Classification layers have shape (H, W, num_classes) where num_classes != 64
        cls_infos = sorted([info for info in out_infos if info.shape[2] != 64], key=lambda x: x.shape[0], reverse=True)
        
        self._out_names = []
        for r, c in zip(reg_infos, cls_infos):
            self._out_names.extend([r.name, c.name])
            
        print(f"[YOLO] Dynamically resolved output names: {self._out_names}")

        # activate network group ค้างไว้ตลอด
        self._activated = self.ng.activate(self.ng.create_params())
        self._activated.__enter__()
        print(f"[YOLO] ready ✓  input={self.input_w}x{self.input_h}")

        # PID
        self.Kp_pan=1.5;  self.Ki_pan=0.0009; self.Kd_pan=1.2
        self.max_step_pan=3.5; self.dead_zone_pan=0.03; self.alpha_pan=0.9
        self.Kp_tilt=0.8; self.Ki_tilt=0.0003; self.Kd_tilt=0.15
        self.max_step_tilt=2.5; self.dead_zone_tilt=0.025; self.alpha_tilt=0.85
        self.last_pan=0.0; self.last_tilt=0.0
        self.pan_integral=0; self.tilt_integral=0
        self.last_pan_error=0; self.last_tilt_error=0
        self.pan_error_history=[]; self.tilt_error_history=[]
        self.pan_history_size=3; self.tilt_history_size=3
        self.tilt_change_limit=1.2

        # SORT
        self.tracker = Sort(max_age=10, min_hits=5, iou_threshold=0.3)
        self.track_id_map={}; self.available_ids=[]
        self.next_display_id=1; self.max_display_id=30
        self.selected_id=None; self.last_detection=[]
        self.last_target_bbox=None

    def _infer(self, frame_bgr):
        img = cv2.resize(frame_bgr, (self.input_w, self.input_h))
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        
        if self.manual_normalize:
            img_normalized = img.astype(np.float32) / 255.0
            inp = {self._inp_name: img_normalized[None]}  # (1,H,W,3) FLOAT32
        else:
            inp = {self._inp_name: img[None]}  # (1,H,W,3) UINT8

        with InferVStreams(self.ng, self.in_params, self.out_params) as pipe:
            raw = pipe.infer(inp)

        reg_list, cls_list = [], []
        for i in range(3):
            r = raw[self._out_names[i*2]][0]
            c = raw[self._out_names[i*2+1]][0]
            reg_list.append(r.reshape(-1, REG_MAX*4))
            cls_list.append(c.reshape(-1, c.shape[-1]))
            
        results, max_conf = decode_outputs(reg_list, cls_list)
        self.last_max_conf = max_conf
        return results

    def detect_and_draw(self, frame, pantilt=None, allow_tracking=True):
        if pantilt:
            self.last_pan = pantilt.get_pan()
            self.last_tilt = pantilt.get_tilt()
        h, w = frame.shape[:2]
        sx, sy = w/INPUT_SIZE, h/INPUT_SIZE

        results    = self._infer(frame)
        detections = []
        for r in results:
            x1,y1,x2,y2 = int(r[0]*sx),int(r[1]*sy),int(r[2]*sx),int(r[3]*sy)
            conf  = float(r[4])
            label = self.CLASS_NAMES[int(r[5])] if int(r[5])<len(self.CLASS_NAMES) else "obj"
            if label not in self.target_classes: continue
            detections.append({"class":label,"conf":conf,"bbox":[x1,y1,x2,y2],"track":False})

        # SORT
        dets_np = np.array([[*d["bbox"],d["conf"]] for d in detections]) if detections else np.empty((0,5))
        tracked = self.tracker.update(dets_np)
        cur_ids = []; trackid_to_det = {}

        draw_targets = []

        for x1,y1,x2,y2,tid in tracked:
            tid=int(tid); cur_ids.append(tid)
            if tid not in self.track_id_map:
                did = self.available_ids.pop(0) if self.available_ids else self.next_display_id
                self.next_display_id = min(self.next_display_id+1, self.max_display_id)
                self.track_id_map[tid] = did
            did = self.track_id_map[tid]
            matched = next((d for d in detections
                            if (lambda b: max(0,min(x2,b[2])-max(x1,b[0]))*
                                         max(0,min(y2,b[3])-max(y1,b[1]))/
                                         max(1,(x2-x1)*(y2-y1))>0.5)(d["bbox"])), None)
            if matched:
                matched["display_id"]=did
                if self.selected_id is None or did==self.selected_id:
                    matched["track"]=True
                trackid_to_det[tid]=matched
            color=(0,180,255) if matched and matched.get("track") else (180,180,180)
            lbl=f"{matched['class'].capitalize()} {did}: {matched['conf']:.2f}" if matched else f"ID {did}"
            
            draw_targets.append(((int(x1), int(y1)), (int(x2), int(y2)), color, lbl))
            
            cv2.rectangle(frame,(int(x1),int(y1)),(int(x2),int(y2)),color,2)
            cv2.putText(frame,lbl,(int(x1),int(y1)-8),cv2.FONT_HERSHEY_SIMPLEX,0.55,color,2)

        self.draw_targets = draw_targets

        for tid in [t for t in self.track_id_map if t not in cur_ids]:
            self.available_ids.insert(0,self.track_id_map.pop(tid))

        # find target
        target = None
        if self.selected_id:
            for d in trackid_to_det.values():
                if d.get("display_id")==self.selected_id: target=d["bbox"]; break
        if target is None and self.last_target_bbox is not None and len(tracked)>0:
            lx=(self.last_target_bbox[0]+self.last_target_bbox[2])/2
            ly=(self.last_target_bbox[1]+self.last_target_bbox[3])/2
            best,bd=None,float("inf")
            for x1,y1,x2,y2,_ in tracked:
                d=((x1+x2)/2-lx)**2+((y1+y2)/2-ly)**2
                if d<bd: bd=d; best=[x1,y1,x2,y2]
            target=best
        elif target is None and len(tracked)>0:
            cx, cy = w / 2, h / 2
            best, min_dist = None, float("inf")
            for x1, y1, x2, y2, _ in tracked:
                tx = (x1 + x2) / 2
                ty = (y1 + y2) / 2
                dist = (tx - cx)**2 + (ty - cy)**2
                if dist < min_dist:
                    min_dist = dist
                    best = [x1, y1, x2, y2]
            target = best
        elif target is None and detections:
            cx, cy = w / 2, h / 2
            best, min_dist = None, float("inf")
            for d in detections:
                x1, y1, x2, y2 = d["bbox"]
                tx = (x1 + x2) / 2
                ty = (y1 + y2) / 2
                dist = (tx - cx)**2 + (ty - cy)**2
                if dist < min_dist:
                    min_dist = dist
                    best = d["bbox"]
            target = best
        self.last_target_bbox=target

        now=time.time()
        if target is None:
            if now-getattr(self,"_ls",now)>3.0: 
                self.selected_id=None; self._ls=now
                # Auto-Search: trigger sweep scan if manual_mgr is configured and not in manual mode
                if self.manual_mgr and not self.manual_mgr.manual_mode and not self.manual_mgr.scan_in_progress:
                    print("[Auto-Search] Target lost for 3s. Starting auto-sweep scan...")
                    self.manual_mgr._start_scan()
            if now-getattr(self,"_lsa",now)>5.0:
                self.track_id_map.clear(); self.available_ids.clear()
                self.next_display_id=1; self._lsa=now
        else:
            self._ls=now; self._lsa=now

        self.no_object_detected = not detections
        if not detections:
            txt="No object detected"
            (tw,th2),bl=cv2.getTextSize(txt,cv2.FONT_HERSHEY_COMPLEX,0.6,1)
            xo=(w-tw)//2; yo=200
            ov=frame.copy()
            cv2.rectangle(ov,(xo-10,yo-th2-10),(xo+tw+10,yo+bl+10),(50,50,50),-1)
            cv2.addWeighted(ov,0.6,frame,0.4,0,frame)
            cv2.putText(frame,txt,(xo,yo),cv2.FONT_HERSHEY_COMPLEX,0.6,(255,255,255),1)

        if self.ws_loop:
            async def _send():
                try:
                    await send_detection({"detections":detections,
                                          "pantilt":{"pan":self.last_pan,"tilt":self.last_tilt}})
                except Exception as ex:
                    print(f"\n[WS-TX Error] Exception in _send: {ex}")
            asyncio.run_coroutine_threadsafe(_send(),self.ws_loop)

        if pantilt and target is not None and allow_tracking:
            self.last_pan=pantilt.get_pan(); self.last_tilt=pantilt.get_tilt()
            self._track_target(target,(w,h),pantilt)

        self.last_detection=detections
        return frame

    def draw_only(self, frame):
        h, w = frame.shape[:2]
        for pt1, pt2, color, lbl in getattr(self, "draw_targets", []):
            cv2.rectangle(frame, pt1, pt2, color, 2)
            cv2.putText(frame, lbl, (pt1[0], pt1[1]-8), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)

        if getattr(self, "no_object_detected", False):
            txt="No object detected"
            (tw,th2),bl=cv2.getTextSize(txt,cv2.FONT_HERSHEY_COMPLEX,0.6,1)
            xo=(w-tw)//2; yo=200
            ov=frame.copy()
            cv2.rectangle(ov,(xo-10,yo-th2-10),(xo+tw+10,yo+bl+10),(50,50,50),-1)
            cv2.addWeighted(ov,0.6,frame,0.4,0,frame)
            cv2.putText(frame,txt,(xo,yo),cv2.FONT_HERSHEY_COMPLEX,0.6,(255,255,255),1)
        return frame


    def _smooth_error(self,error,history,size=3):
        history.append(error)
        if len(history)>size: history.pop(0)
        return sum(history)/len(history)

    def _track_target(self,bbox,frame_size,pantilt):
        x1,y1,x2,y2=bbox; fw,fh=frame_size
        dx=self._smooth_error((((x1+x2)/2)-fw/2)/(fw/2),self.pan_error_history,self.pan_history_size)
        dy=self._smooth_error((((y1+y2)/2)-fh/2)/(fh/2),self.tilt_error_history,self.tilt_history_size)
        if abs(dx)<self.dead_zone_pan: dx=0
        if abs(dy)<self.dead_zone_tilt: dy=0
        if dx==0 and dy==0: return
        self.pan_integral=max(-5,min(5,self.pan_integral+dx))
        po=self.Kp_pan*dx+self.Ki_pan*self.pan_integral+self.Kd_pan*(dx-self.last_pan_error)
        em=abs(dx); ds=self.max_step_pan*(0.8 if em>0.4 else 0.6 if em>0.2 else 0.4 if em>0.1 else 0.2)
        safe_pan=(self.last_pan*(1-self.alpha_pan)+(self.last_pan+max(-ds,min(ds,self.pan_direction * po)))*self.alpha_pan)%360
        pantilt.set_pan(safe_pan); self.last_pan=safe_pan; self.last_pan_error=dx
        self.tilt_integral=max(-2,min(2,self.tilt_integral+dy))
        to=self.Kp_tilt*dy+self.Ki_tilt*self.tilt_integral+\
           self.Kd_tilt*((dy-self.last_tilt_error)*0.3+self.last_tilt_error*0.7)
        em=abs(dy); ds=self.max_step_tilt*(0.6 if em>0.3 else 0.4 if em>0.15 else 0.25 if em>0.08 else 0.1)
        dt=max(-ds,min(ds,self.tilt_direction * to))
        if abs(dt)>self.tilt_change_limit: dt=self.tilt_change_limit*(1 if dt>0 else -1)
        safe_tilt=max(-75,min(40,self.last_tilt*(1-self.alpha_tilt)+(self.last_tilt+dt)*self.alpha_tilt))
        pantilt.set_tilt(safe_tilt); self.last_tilt=safe_tilt; self.last_tilt_error=dy

        # Throttled debug log to prevent terminal flooding
        if not hasattr(self, "_log_counter"):
            self._log_counter = 0
        self._log_counter += 1
        if self._log_counter % 10 == 0:
            print(f"\n[Tracker] Target Box: {[int(b) for b in bbox]} | Error: dx={dx:.3f}, dy={dy:.3f} | CMD -> Pan: {safe_pan:.2f}, Tilt: {safe_tilt:.2f}")


    def close(self):
        self._activated.__exit__(None,None,None)