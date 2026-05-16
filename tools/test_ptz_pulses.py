import time
import requests

# --- CONFIGURATION ---
CAMERA_IP = "192.168.1.118"  # Change to your camera's IP
USERNAME = "admin"
PASSWORD = "your_password_here"
# ---------------------

session = requests.Session()
session.auth = (USERNAME, PASSWORD)

def send_cmd(cmd_id):
    url = f"http://{CAMERA_IP}/form/setPTZCfg"
    params = {"command": cmd_id, "panSpeed": 7, "tiltSpeed": 6}
    try:
        r = session.get(url, params=params, timeout=1)
        print(f"📡 Sent command {cmd_id} -> HTTP Status: {r.status_code}")
    except Exception as e:
        print(f"❌ Connection failed: {e}")

def test_pulse(direction_name, start_cmd, duration=0.25):
    print(f"\n🎬 Testing {direction_name.upper()} pulse for {duration}s...")
    send_cmd(start_cmd)
    time.sleep(duration)
    send_cmd(0)
    print("🛑 Sent STOP command.")

if __name__ == "__main__":
    print("=== Budget PTZ Pulse Test Utility ===")
    print("WARNING: Keep your hands clear of the camera mechanism.")
    print("Press Enter to run a 0.25-second LEFT pulse...")
    input()
    
    # Test a left move (Command 3)
    test_pulse(direction_name="left", start_cmd=3, duration=0.25)
    print("\n✅ Test complete!")
