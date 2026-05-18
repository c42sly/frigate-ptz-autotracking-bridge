🚧 Work in Progress: 
ONVIF Zoom EmulationThis document tracks the ongoing engineering efforts to map modern ONVIF Zoom commands to the legacy HTTP CGI commands used by budget Xiongmai (XM) OEM camera chipsets.

📊 Current Status MatrixClientPan / Tilt SupportZoom SupportStatusONVIF Device Manager (ODM)
🟢 Fully Functional
🟢 Functional (CGI Commands 13/14)Stable BaselineFrigate NVR (AI Autotracking)
🟢 Fully Functional
🔴 In Development (Parser Conflicts)Work In Progress

🔬 The Core Engineering ChallengeXM chipsets handle physical optical zoom via brute-force legacy HTTP endpoints (/form/setPTZCfg?command=13 for Zoom In, and 14 for Zoom Out).Modern AI platforms like Frigate expect a highly sophisticated, multi-layered ONVIF schema definition to map coordinates. Frigate’s underlying python communication engine (onvif-zeep) enforces strict WSDL schema sequence checks, making it significantly more sensitive to custom XML responses than standard tools like ONVIF Device Manager.
📓 Attempted Implementations Log

🛑 Attempt 1: Bare Minimum Emulation (No Zoom Tags)Strategy: Emulate only the Pan/Tilt spaces and completely omit any zoom blocks from the templates.ODM Result: Permissive success. ODM grayed out the zoom slider but allowed full pan/tilt control.Frigate Result: Failure. The application thread crashed on boot with a fatal KeyError: 'absolute_zoom_range'. Frigate's tracking client strictly requires a zoom boundary declaration to initialize.

🛑 Attempt 2: Direct Profile InjectionStrategy: Inject <tt:AbsoluteZoomPositionSpace> parameters straight into the main <tt:PTZConfiguration> block inside GetProfiles.ODM Result: Success. The zoom slider unlocked in ODM and fired working relative zoom pulses.Frigate Result: Failure. Frigate threw a silent validation error: No appropriate Onvif profiles found. Because the custom tags altered the strict XML element sequence required by the ONVIF WSDL specifications, Frigate rejected the entire camera profile.

🟡 Attempt 3: Isolated Node Mapping (Current Phase)Strategy: Keep the profile payloads minimal, but map the specific absolute, relative, and continuous zoom boundary blocks inside GetNodes and GetConfigurationOptions.ODM Result: Success. Fully compatible.Frigate Result: Unstable. Bypasses the initial profile check but triggers intermittent parser rejections depending on the environment build cache.🔮 Next Steps & Research TrajectoryWSDL Sequence Audit: Deconstruct the official ONVIF PTZ schema to identify the exact, byte-perfect sequence alignment required for <tt:PTZConfiguration> fields to appease Frigate's strict validation layer.Namespace Isolation: Ensure all custom Z_ABS_SPACE and Z_REL_SPACE variables carry pristine schema headers throughout the handshake loop.
