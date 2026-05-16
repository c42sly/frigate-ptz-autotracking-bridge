# Frigate ONVIF Bridge

A lightweight middleware proxy that translates standard ONVIF PTZ commands into proprietary HTTP/CGI API calls, unlocking Frigate autotracking for budget, legacy, or unsupported IP cameras.

## Key Features

* **🎯 Unlocks Frigate Autotracking:** Fully supports Frigate's `RelativeMove` calculations, allowing AI to track objects using cameras that technically don't support it.
* **🧠 Synthetic Software Encoder:** Uses dead-reckoning kinematics to track camera position in virtual space, satisfying Frigate's need for strict coordinate feedback.
* **📐 Vector Decomposition:** Smoothly translates diagonal tracking vectors into sequential pan/tilt motor pulses, bypassing single-axis hardware limitations.
* **🔌 Port Multiplexing:** Hosts independent, isolated ONVIF engines for multiple cameras simultaneously using custom dedicated ports.
* **🐳 Docker Native:** Built to run as a lightweight container that integrates seamlessly into your existing home lab network stack.

## Under the Hood: How it Works

Standard ONVIF hardware expects precise coordinate systems or continuous motion commands. However, many budget PTZ cameras rely on simple, unauthenticated HTTP/CGI endpoints that only understand a rudimentary command structure: "Start moving left" or "Stop moving." 

To bridge this gap and allow Frigate to drive these cameras accurately, the proxy utilizes a **Reverse-Engineered Pulse Engine**.

### 1. Reverse-Engineering the Camera Controls
The HTTP endpoints used in this script were discovered by accessing the camera's native web interface using a browser, opening the **Developer Tools (F12) -> Network Tab**, and clicking the manual control arrows. 

The network traffic revealed that the camera responds to query parameters sent to a specific URL:
`http://<camera_ip>/form/setPTZCfg?command=<id>&panSpeed=<speed>&tiltSpeed=<speed>`

Through testing, the specific numerical mappings for the commands were identified:
* `0`: Stop
* `1`: Up
* `2`: Down
* `3`: Left
* `4`: Right

### 2. The Crucial "Pulse and Stop" Mechanism
Frigate's autotracking engine sends precise *RelativeMove* commands (e.g., "move exactly `x=0.25` to the right"). Because cheap hardware cannot interpret "move 25% to the right," the bridge translates this spatial vector into a temporal pulse:

1. **Calculate Pulse Duration:** The script interprets the vector magnitude and scales it into a precise time window (e.g., 0.15 seconds).
2. **Fire the Movement Command:** The script hits the camera's HTTP endpoint to initiate movement (e.g., `command=3` for Left).
3. **The Precise Sleep:** The Python engine pauses execution for the exact duration of the calculated time window.
4. **Fire the Emergency Stop:** The script immediately fires a follow-up command (`command=0` for Stop). 

> ⚠️ **CRITICAL:** Without the explicit follow-up **Stop** command, the camera will enter an infinite runaway loop, spinning completely out of control until it hits its physical mechanical limit. The "Pulse-and-Stop" loop is the absolute core of this bridge's kinematics engine.

### 3. Handling Different Hardware Variations
Not all budget cameras use the exact CGI paths or command integers documented above. For instance:
* Some brands use JSON payloads over WebSockets rather than URL parameters.
* Some cameras invert the Y-axis commands or require different speed parameters (e.g., scale of 1-100 instead of 1-10).

## 🛠️ Project Status: Proof of Concept

This project started as a raw idea born out of necessity to solve a specific problem. It is **not a polished, commercial-grade product**, but rather a functional **Proof of Concept (PoC)**. 

While it successfully drives autotracking on my specific network setup, the codebase is rough around the edges. It was built to prove a theory, and there are endless ways it could be optimized, refactored, or expanded to support broader hardware.

### 🤝 Contributions & Ideas Welcome!
If you think this is a cool concept, find a bug, or want to make the code prettier, please jump in! You are explicitly invited to:
* **Fork the repository** and adapt it to your own weird hardware.
* **Open an issue** to discuss new features or protocol discoveries.
* **Submit Pull Requests** for performance improvements or cleaner logic.

Let's make budget hardware do cool things together.
To accommodate this, the codebase isolates the hardware execution loop. If your camera uses a different API, you can swap out the endpoint URLs and command IDs within the `_send_http_cmd` function to match your specific hardware's API structure.
