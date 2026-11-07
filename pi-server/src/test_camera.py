import time
import cv2
import numpy as np
from picamera2 import Picamera2

def main():
    print("Initializing Picamera2...")
    picam2 = Picamera2()
    
    # Configure for 1280x720 capture
    cfg = picam2.create_video_configuration(
        main={"size": (1280, 720), "format": "RGB888"}
    )
    picam2.configure(cfg)
    
    print("Starting camera...")
    picam2.start()
    
    # Try enabling Continuous AutoFocus
    try:
        picam2.set_controls({"AfMode": 2})  # 2 = Continuous AutoFocus
        print("Continuous AutoFocus (AF) enabled.")
    except Exception as e:
        print(f"Warning: Could not enable autofocus: {e}")
        
    print("Waiting 3 seconds for focus to settle...")
    time.sleep(3)
    
    print("Capturing frame...")
    frame = picam2.capture_array()
    
    if frame is not None:
        # Convert RGB to BGR for saving with OpenCV
        bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        # Rotate 180 degrees because the camera is mounted upside down
        bgr = cv2.rotate(bgr, cv2.ROTATE_180)
        
        filename = "test_image.jpg"
        cv2.imwrite(filename, bgr)
        print(f"\nSUCCESS: Image saved as '{filename}'!")
    else:
        print("\nERROR: Captured frame is empty.")
        
    print("Stopping camera...")
    picam2.stop()

if __name__ == "__main__":
    main()
