## Deep Dive: Reverse-Engineering via Clean-Room Packet Captures

If your camera lacks a standard web configuration page or hides its PTZ endpoint data, you can discover its control API using raw packet analysis. The endpoints for this project were mapped using an isolated network sandbox ("clean room") to eliminate background noise.

(Method 1: The Physical Sandbox (Simplest & Most Reliable)
This requires completely air-gapping your target devices from your main local network.

The Hardware: You need a standalone, unmanaged switch (or an old spare router completely disconnected from any internet/WAN link), an Ethernet cable for the camera, and your workstation/server.

The Configuration:

Connect your computer's ethernet port and the camera directly into the isolated switch.

Assign static IP addresses to both devices on a completely distinct subnet that doesn't match your house network (e.g., set your laptop to 192.168.99.10 and the camera to 192.168.99.20).

Close all background applications on your workstation, turn off its Wi-Fi connection, and close system sync daemons to ensure your computer isn't polluting the logs.)



### Why a Clean Room Setup is Necessary
Cheap IoT devices and IP cameras generate a massive volume of telemetry and cloud broadcast traffic. Running a packet capture on a busy production network makes isolating commands difficult. 

An isolated setup involves connecting only three components to a dumb switch or dedicated access point:
1. The target camera.
2. A workstation running a mobile app or web client to trigger movements.
3. A packet capture interface.

This eliminates all DNS, NTP, and external cloud polling, leaving a completely silent network where only explicit control commands are captured.

### The Capture Methodology

1. **Start the Sniffer:** With the camera booted in the isolated subnet, target its IP address using `tcpdump` from the terminal to log all raw HTTP/TCP traffic to a capture file:
   ```bash
   sudo tcpdump -i eth0 host 192.168.1.118 -vv -A -w camera_capture.pcap

   ## Shortcuts: Using AI to Decode Your Raw Logs

If you have used `tcpdump` and `strings` to dump your camera's network traffic but are struggling to spot the exact URLs or command patterns, you can use an AI LLM (like ChatGPT, Claude, or Gemini) to parse the data for you. 

Copy and paste the following prompt templates, filling in your raw terminal output.

### Prompt 1: Extracting the Hidden Endpoints
Use this prompt to have the AI scan through pages of messy terminal dumps to extract only the lines that look like web control commands.

> **Copy/Paste Prompt:**
> "I am reverse-engineering an unbranded IP camera to find its manual PTZ control commands. I ran an isolated packet capture while pressing the directional arrows in the camera app, and used `strings` to dump the text. 
>
> Below is the raw network dump. Please analyze it and extract any HTTP GET/POST URLs, query parameters, or JSON payloads that look like they are sending commands (look for keywords like `ptz`, `cmd`, `motor`, `move`, `set`, `control`, or speed values).
> 
> [PASTE YOUR RAW STRINGS/GREP OUTPUT HERE]"

### Prompt 2: Mapping the Directional Integers
Once the AI gives you the URLs, budget cameras usually hide the directions behind obscure numbers (e.g., `command=3`). Use this prompt to figure out which number controls which direction.

> **Copy/Paste Prompt:**
> "I have isolated the PTZ control endpoint for my IP camera. It looks like this: `http://192.168.1.118/form/setPTZCfg?command=X`. 
> 
> During my packet capture, I pressed the physical app buttons in this exact chronological order with 5-second pauses between them: LEFT, RIGHT, UP, DOWN. 
> 
> Looking at the timestamps of the captured requests below, help me deduce which integer value of 'X' maps to which physical direction, and identify which command represents the 'STOP' command.
>
> [PASTE THE TIME-STAMPED HTTP REQUEST LINES HERE]"

### Prompt 3: Writing the Bridge Integration
Once the AI helps you find the commands, you can use this prompt to generate the exact Python code snippet needed to plug your new camera into this bridge framework.

> **Copy/Paste Prompt:**
> "I have successfully reverse-engineered my PTZ camera. It uses basic HTTP GET requests for movement. 
> * Base URL: `http://<IP>/device/ptz.cgi`
> * Parameters: `dir` (values: 1=up, 2=down, 3=left, 4=right, 0=stop) and `speed` (scale 1-10)
> 
> Please write a Python method called `_send_hardware_cmd(direction, speed)` using the `requests` library that maps a directional string input to these specific URL parameters, ensuring it handles HTTP basic authentication."
