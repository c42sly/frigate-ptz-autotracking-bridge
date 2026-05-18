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
    params = {"command": cmd_id, "panSpeed": 2, "tiltSpeed": 6}
    try:
        session.get(url, params=params, timeout=1)
    except Exception as e:
        print(f"❌ Network error: {e}")

def run_tuning():
    print("=== PTZ Pulse Tuning Utility ===")
    print("This tool sends a blind, precisely timed pulse to calculate motor velocity.")
    print("-------------------------------------------------------------------------")
    
    while True:
        print("\n👉 STEP 1: Use your camera app to send the camera to PRESET 1 (North).")
        input("Once the camera has completely stopped at Preset 1, press ENTER...")
        
        try:
            duration_input = input("\n👉 STEP 2: Enter pulse duration to test in seconds (e.g., 1.5) -> ")
            test_duration = float(duration_input)
        except ValueError:
            print("❌ Invalid number. Please enter a decimal value.")
            continue

        print(f"\n🎬 Firing RIGHT pulse for exactly {test_duration} seconds...")
        
        # 1. Start moving right (Command 4)
        send_cmd(1)
        
        # 2. Hold the exact duration on the system clock
        time.sleep(test_duration)
        
        # 3. Fire the automatic stop
        send_cmd(0)
        print("🛑 STOP command sent automatically.")
        
        print("\n👉 STEP 3: Check your live video feed.")
        print("- If it landed perfectly on PRESET 2 (East): Your calibration is done!")
        # Math: 90 degrees divided by your successful time
        calculated_speed = 90.0 / test_duration
        print(f"  🏆 YOUR CALIBRATED PAN SPEED IS: {calculated_speed:.2f} degrees/sec")
        print(f"  (Put {test_duration} into your kinematics engine as your 90-degree baseline)")
        
        print("- If it stopped SHORT of East: You need a LARGER duration value.")
        print("- If it OVERSHOT East: You need a SMALLER duration value.")
        
        choice = input("\nWould you like to test another duration? (y/n) -> ").lower()
        if choice != 'y':
            print("Tuning complete. Keep tinkering!")
            break

if __name__ == "__main__":
    run_tuning()
