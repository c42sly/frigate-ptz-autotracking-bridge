# Frigate ONVIF Bridge

A lightweight middleware proxy that translates standard ONVIF PTZ commands into proprietary HTTP/CGI API calls, unlocking Frigate autotracking for budget, legacy, or unsupported IP cameras.

## Key Features

* **🎯 Unlocks Frigate Autotracking:** Fully supports Frigate's `RelativeMove` calculations, allowing AI to track objects using cameras that technically don't support it.
* **🧠 Synthetic Software Encoder:** Uses dead-reckoning kinematics to track camera position in virtual space, satisfying Frigate's need for strict coordinate feedback.
* **📐 Vector Decomposition:** Smoothly translates diagonal tracking vectors into sequential pan/tilt motor pulses, bypassing single-axis hardware limitations.
* **🔌 Port Multiplexing:** Hosts independent, isolated ONVIF engines for multiple cameras simultaneously using custom dedicated ports.
* **🐳 Docker Native:** Built to run as a lightweight container that integrates seamlessly into your existing home lab network stack.
