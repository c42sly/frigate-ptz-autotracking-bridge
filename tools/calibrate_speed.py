import time
import requests

# --- CONFIGURATION ---
CAMERA_IP = "192.168.1.118"
USERNAME = "admin"
PASSWORD = "fordsyke1944"
# ---------------------

session = requests.Session()
session.auth = (USERNAME, PASSWORD)

def send_cmd(cmd_id):
    url = f"http://{CAMERA_IP}/form/setPTZCfg"
    params = {"command": cmd_id, "panSpeed": 7, "tiltSpeed": 6}
    session.get(url, params=params, timeout=1)

def run_calibration():
    print("=== PTZ Speed Calibration Tool ===")
    print("1. Ensuring camera is at Preset 1 (North, 0 degrees)...")
    # Call your native preset 1 command here 
    # (Assuming your camera app handles the preset move)
    print("👉 Please verify the camera has fully stopped at Preset 1.")
    
    input("Press ENTER to start the continuous RIGHT movement...")
    
    # Start moving right (Command 4)
    start_time = time.time()
    send_cmd(4)
    
    print("\n🚀 Camera is moving right!")
    print("👉 WATCH YOUR LIVE FEED.")
    input("Press ENTER the EXACT millisecond the camera hits Preset 2 (East, 90 degrees) -> ")
    
    # Stop immediately (Command 0)
    send_cmd(0)
    end_time = time.time()
    
    elapsed_time = end_time - start_time
    print("\n🛑 Stopped.")
    print(f"⏱️ Total Transit Time: {elapsed_time:.3f} seconds")
    
    # Math: 90 degrees divided by total seconds
    degrees_per_second = 90.0 / elapsed_time
    print(f"📊 Calculated Pan Speed: {degrees_per_second:.2f} degrees per second")
    
    print("\nTo double-check this, a 45-degree turn should take exactly:")
    print(f"⏳ {45.0 / degrees_per_second:.3f} seconds")

if __name__ == "__main__":
    run_calibration()
