import os
import time
import threading
import queue
import requests
import logging
import yaml
import xml.etree.ElementTree as ET
from flask import Flask, request, Response
from werkzeug.serving import make_server

# Silence Flask internal HTTP logging to keep console output readable
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

NAMESPACES = {
    'SOAP-ENV': 'http://www.w3.org/2003/05/soap-envelope',
    'tt': 'http://www.onvif.org/ver10/schema',
    'tptz': 'http://www.onvif.org/ver20/ptz/wsdl',
    'trt': 'http://www.onvif.org/ver10/media/wsdl',
    'tds': 'http://www.onvif.org/ver10/device/wsdl'
}
for prefix, uri in NAMESPACES.items():
    ET.register_namespace(prefix, uri)

# Expanded command mappings containing your reverse-engineered zoom targets
CMDS = {"stop": 0, "up": 1, "down": 2, "left": 3, "right": 4, "zoom_in": 13, "zoom_out": 14}

# Strict compliance token definitions matching across all schemas
P_TOKEN = "Profile_1"
PTZ_C_TOKEN = "PTZConfig_1"
N_TOKEN = "PTZNode_1"
V_SRC_TOKEN = "VideoSourceToken_1"
V_ENC_TOKEN = "VideoEncoderToken_1"

# Canonical ONVIF Space URIs required for relative and continuous zoom layers
PT_VEL_SPACE = "http://www.onvif.org/ver10/tptz/PanTiltSpaces/VelocityGenericSpace"
PT_REL_SPACE = "http://www.onvif.org/ver10/tptz/PanTiltSpaces/TranslationSpaceFov"
Z_VEL_SPACE = "http://www.onvif.org/ver10/tptz/ZoomSpaces/VelocityGenericSpace"
Z_REL_SPACE = "http://www.onvif.org/ver10/tptz/ZoomSpaces/TranslationGenericSpace"

# Reusable internal profile payload block
PROFILE_PAYLOAD = f"""<tt:Name>MainProfile</tt:Name>
<tt:VideoSourceConfiguration token="{V_SRC_TOKEN}">
<tt:Name>VideoSource</tt:Name>
<tt:UseCount>1</tt:UseCount>
<tt:SourceToken>{V_SRC_TOKEN}</tt:SourceToken>
<tt:Bounds x="0" y="0" width="1920" height="1080"/>
</tt:VideoSourceConfiguration>
<tt:VideoEncoderConfiguration token="{V_ENC_TOKEN}">
<tt:Name>Encoder</tt:Name>
<tt:UseCount>1</tt:UseCount>
<tt:Encoding>H264</tt:Encoding>
<tt:Resolution><tt:Width>1920</tt:Width><tt:Height>1080</tt:Height></tt:Resolution>
<tt:Quality>1.0</tt:Quality>
<tt:RateControl>
<tt:FrameRateLimit>15</tt:FrameRateLimit>
<tt:EncodingInterval>1</tt:EncodingInterval>
<tt:BitrateLimit>2048</tt:BitrateLimit>
</tt:RateControl>
<tt:Multicast>
<tt:Address><tt:Type>IPv4</tt:Type><tt:IPv4Address>0.0.0.0</tt:IPv4Address></tt:Address>
<tt:Port>0</tt:Port><tt:TTL>1</tt:TTL><tt:AutoStart>false</tt:AutoStart>
</tt:Multicast>
<tt:SessionTimeout>PT10S</tt:SessionTimeout>
</tt:VideoEncoderConfiguration>
<tt:PTZConfiguration token="{PTZ_C_TOKEN}">
<tt:Name>PTZConfig</tt:Name>
<tt:UseCount>1</tt:UseCount>
<tt:NodeToken>{N_TOKEN}</tt:NodeToken>
<tt:DefaultRelativePanTiltTranslationSpace>{PT_REL_SPACE}</tt:DefaultRelativePanTiltTranslationSpace>
<tt:DefaultContinuousPanTiltVelocitySpace>{PT_VEL_SPACE}</tt:DefaultContinuousPanTiltVelocitySpace>
<tt:DefaultRelativeZoomTranslationSpace>{Z_REL_SPACE}</tt:DefaultRelativeZoomTranslationSpace>
<tt:DefaultContinuousZoomVelocitySpace>{Z_VEL_SPACE}</tt:DefaultContinuousZoomVelocitySpace>
</tt:PTZConfiguration>"""

# Pre-compiled compliance templates separating raw XML text from logic flows
SOAP_TEMPLATES = {
    "GetSystemDateAndTime": """<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope" xmlns:tds="http://www.onvif.org/ver10/device/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema">
<SOAP-ENV:Body><tds:GetSystemDateAndTimeResponse><tds:SystemDateAndTime>
<tt:DateTimeType>NTP</tt:DateTimeType><tt:DaylightSavings>false</tt:DaylightSavings>
<tt:TimeZone><tt:TZ>GMT</tt:TZ></tt:TimeZone>
<tt:UTCDateTime><tt:Time><tt:Hour>12</tt:Hour><tt:Minute>0</tt:Minute><tt:Second>0</tt:Second></tt:Time>
<tt:Date><tt:Year>2026</tt:Year><tt:Month>5</tt:Month><tt:Day>17</tt:Day></tt:Date></tt:UTCDateTime>
</tds:SystemDateAndTime></tds:GetSystemDateAndTimeResponse></SOAP-ENV:Body></SOAP-ENV:Envelope>""",

    "GetVideoSources": f"""<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope" xmlns:trt="http://www.onvif.org/ver10/media/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema">
<SOAP-ENV:Body><trt:GetVideoSourcesResponse><trt:VideoSources token="{V_SRC_TOKEN}">
<tt:Framerate>15</tt:Framerate><tt:Resolution><tt:Width>1920</tt:Width><tt:Height>1080</tt:Height></tt:Resolution>
</trt:VideoSources></trt:GetVideoSourcesResponse></SOAP-ENV:Body></SOAP-ENV:Envelope>""",

    "GetScopes": """<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope" xmlns:tds="http://www.onvif.org/ver10/device/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema">
<SOAP-ENV:Body><tds:GetScopesResponse>
<tds:Scopes><tt:ScopeDefinition>Fixed</tt:ScopeDefinition><tt:ScopeItem>onvif://www.onvif.org/type/video_encoder</tt:ScopeItem></tds:Scopes>
<tds:Scopes><tt:ScopeDefinition>Fixed</tt:ScopeDefinition><tt:ScopeItem>onvif://www.onvif.org/type/ptz</tt:ScopeItem></tds:Scopes>
</tds:GetScopesResponse></SOAP-ENV:Body></SOAP-ENV:Envelope>""",

    "GetNetworkInterfaces": """<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope" xmlns:tds="http://www.onvif.org/ver10/device/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema">
<SOAP-ENV:Body><tds:GetNetworkInterfacesResponse><tds:NetworkInterfaces token="eth0">
<tt:Enabled>true</tt:Enabled>
<tt:Info><tt:Name>eth0</tt:Name><tt:HwAddress>00:11:22:33:44:55</tt:HwAddress></tt:Info>
</tds:NetworkInterfaces></tds:GetNetworkInterfacesResponse></SOAP-ENV:Body></SOAP-ENV:Envelope>""",

    "GetDeviceInformation": f"""<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope" xmlns:tds="http://www.onvif.org/ver10/device/wsdl">
<SOAP-ENV:Body><tds:GetDeviceInformationResponse>
<tds:Manufacturer>FrigateProxy</tds:Manufacturer><tds:Model>VirtualPTZEngine</tds:Model>
<tds:FirmwareVersion>2.0</tds:FirmwareVersion><tds:SerialNumber>CAMERA_NAME</tds:SerialNumber>
<tds:HardwareId>VirtualPTZ</tds:HardwareId>
</tds:GetDeviceInformationResponse></SOAP-ENV:Body></SOAP-ENV:Envelope>""",

    "GetCapabilities": """<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope" xmlns:tds="http://www.onvif.org/ver10/device/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema">
<SOAP-ENV:Body><tds:GetCapabilitiesResponse><tds:Capabilities>
<tt:Device><tt:XAddr>DEV_URI</tt:XAddr></tt:Device>
<tt:Media><tt:XAddr>MED_URI</tt:XAddr></tt:Media>
<tt:PTZ><tt:XAddr>PTZ_URI</tt:XAddr></tt:PTZ>
</tds:Capabilities></tds:GetCapabilitiesResponse></SOAP-ENV:Body></SOAP-ENV:Envelope>""",

    "GetProfiles": f"""<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope" xmlns:trt="http://www.onvif.org/ver10/media/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema">
<SOAP-ENV:Body><trt:GetProfilesResponse><trt:Profiles fixed="true" token="{P_TOKEN}">
{PROFILE_PAYLOAD}
</trt:Profiles></trt:GetProfilesResponse></SOAP-ENV:Body></SOAP-ENV:Envelope>""",

    "GetProfile": f"""<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope" xmlns:trt="http://www.onvif.org/ver10/media/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema">
<SOAP-ENV:Body><trt:GetProfileResponse><trt:Profile fixed="true" token="{P_TOKEN}">
{PROFILE_PAYLOAD}
</trt:Profile></trt:GetProfileResponse></SOAP-ENV:Body></SOAP-ENV:Envelope>""",

    "GetConfigurations": f"""<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope" xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema">
<SOAP-ENV:Body><tptz:GetConfigurationsResponse><tptz:PTZConfiguration token="{PTZ_C_TOKEN}">
<tt:Name>PTZConfig</tt:Name><tt:UseCount>1</tt:UseCount><tt:NodeToken>{N_TOKEN}</tt:NodeToken>
</tptz:PTZConfiguration></tptz:GetConfigurationsResponse></SOAP-ENV:Body></SOAP-ENV:Envelope>""",

    "GetNodes": f"""<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope" xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema">
<SOAP-ENV:Body><tptz:GetNodesResponse><tptz:PTZNode token="{N_TOKEN}">
<tt:Name>BasePTZNode</tt:Name><tt:SupportedPTZSpaces>
<tt:RelativePanTiltTranslationSpace>
<tt:URI>{PT_REL_SPACE}</tt:URI>
<tt:XRange><tt:Min>-1</tt:Min><tt:Max>1</tt:Max></tt:XRange><tt:YRange><tt:Min>-1</tt:Min><tt:Max>1</tt:Max></tt:YRange>
</tt:RelativePanTiltTranslationSpace>
<tt:ContinuousPanTiltVelocitySpace>
<tt:URI>{PT_VEL_SPACE}</tt:URI>
<tt:XRange><tt:Min>-1</tt:Min><tt:Max>1</tt:Max></tt:XRange><tt:YRange><tt:Min>-1</tt:Min><tt:Max>1</tt:Max></tt:YRange>
</tt:ContinuousPanTiltVelocitySpace>
<tt:RelativeZoomTranslationSpace>
<tt:URI>{Z_REL_SPACE}</tt:URI><tt:XRange><tt:Min>-1</tt:Min><tt:Max>1</tt:Max></tt:XRange>
</tt:RelativeZoomTranslationSpace>
<tt:ContinuousZoomVelocitySpace>
<tt:URI>{Z_VEL_SPACE}</tt:URI><tt:XRange><tt:Min>-1</tt:Min><tt:Max>1</tt:Max></tt:XRange>
</tt:ContinuousZoomVelocitySpace>
</tt:SupportedPTZSpaces>
<tt:MaximumNumberOfPresets>10</tt:MaximumNumberOfPresets><tt:HomeSupported>true</tt:HomeSupported>
</tptz:PTZNode></tptz:GetNodesResponse></SOAP-ENV:Body></SOAP-ENV:Envelope>""",

    "GetConfigurationOptions": f"""<?xml version="1.0" encoding="utf-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope" xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema">
<SOAP-ENV:Body><tptz:GetConfigurationOptionsResponse><tptz:PTZConfigurationOptions><tt:Spaces>
<tt:RelativePanTiltTranslationSpace>
<tt:URI>{PT_REL_SPACE}</tt:URI>
<tt:XRange><tt:Min>-1</tt:Min><tt:Max>1</tt:Max></tt:XRange><tt:YRange><tt:Min>-1</tt:Min><tt:Max>1</tt:Max></tt:YRange>
</tt:RelativePanTiltTranslationSpace>
<tt:ContinuousPanTiltVelocitySpace>
<tt:URI>{PT_VEL_SPACE}</tt:URI>
<tt:XRange><tt:Min>-1</tt:Min><tt:Max>1</tt:Max></tt:XRange><tt:YRange><tt:Min>-1</tt:Min><tt:Max>1</tt:Max></tt:YRange>
</tt:ContinuousPanTiltVelocitySpace>
<tt:RelativeZoomTranslationSpace>
<tt:URI>{Z_REL_SPACE}</tt:URI><tt:XRange><tt:Min>-1</tt:Min><tt:Max>1</tt:Max></tt:XRange>
</tt:RelativeZoomTranslationSpace>
<tt:ContinuousZoomVelocitySpace>
<tt:URI>{Z_VEL_SPACE}</tt:URI><tt:XRange><tt:Min>-1</tt:Min><tt:Max>1</tt:Max></tt:XRange>
</tt:ContinuousZoomVelocitySpace>
</tt:Spaces><tt:PTZTimeout><tt:Min>PT1S</tt:Min><tt:Max>PT120S</tt:Max></tt:PTZTimeout>
</tptz:PTZConfigurationOptions></tptz:GetConfigurationOptionsResponse></SOAP-ENV:Body></SOAP-ENV:Envelope>"""
}

# ==============================================================================
#  THE MOTION ENGINE    
# ==============================================================================
class MotionEngine(threading.Thread):
    def __init__(self, name, config, password):
        super().__init__(daemon=True)
        self.name = name
        self.ip = config['ip']
        self.driver = config.get('driver', 'generic_cgi')
        
        self.session = requests.Session()
        self.session.auth = (config['user'], password)
        
        self.base_url = f"http://{self.ip}/form/setPTZCfg"
        self.preset_url = f"http://{self.ip}/form/presetSet"
        
        self.work_queue = queue.Queue()
        self.is_moving = False
        self.move_end_time = 0.0
        self.lock = threading.Lock()
        
        self.pan_velocity = config.get('pan_velocity', 7)
        self.tilt_velocity = config.get('tilt_velocity', 6)
        self.cooldown_window = config.get('cooldown', 0.35)
        self.scale_multiplier = config.get('scale_multiplier', 0.7)

        self.virtual_pan = 0.0              
        self.virtual_tilt = 0.0             
        self.encoder_speed_factor = 0.15    
        self.last_move_completion = 0.0    

    def run(self):
        while True:
            try:
                move_type, x, y, z = self.work_queue.get(timeout=0.1)
                self._process_movement(move_type, x, y, z)
                self.work_queue.task_done()
            except queue.Empty:
                with self.lock:
                    if self.is_moving and time.time() >= self.move_end_time:
                        self.is_moving = False

    def dispatch_move(self, move_type, x, y, z=0.0):
        self.work_queue.put((move_type, x, y, z))

    def force_stop(self):
        while not self.work_queue.put:
            try:
                self.work_queue.get_nowait()
                self.work_queue.task_done()
            except queue.Empty:
                break
        with self.lock:
            self.is_moving = False
            self.move_end_time = 0.0
        self._send_http_cmd("stop")
        print(f"[{self.name}] 🛑 Emergency Stop Executed")

    def get_engine_status(self):
        with self.lock:
            if self.is_moving and time.time() >= self.move_end_time:
                self.is_moving = False
            return "MOVING" if self.is_moving else "IDLE", self.virtual_pan, self.virtual_tilt

    def _process_movement(self, move_type, x, y, z):
        current_time = time.time()
        print(f"[{self.name}] 📊 Vector Payload -> x={x:.3f} y={y:.3f} z={z:.3f} ({move_type})")

        if move_type == "RELATIVE":
            if (current_time - self.last_move_completion) < self.cooldown_window:
                return  

            pan_dur = max(0.10, min(0.45, abs(x) * self.scale_multiplier)) if abs(x) > 0.05 else 0.0
            tilt_dur = max(0.10, min(0.45, abs(y) * self.scale_multiplier)) if abs(y) > 0.05 else 0.0
            zoom_dur = max(0.10, min(0.45, abs(z) * self.scale_multiplier)) if abs(z) > 0.05 else 0.0
            
            pan_dir = "right" if x > 0 else "left" if x < 0 else "stop"
            tilt_dir = "up" if y > 0 else "down" if y < 0 else "stop"
            zoom_dir = "zoom_in" if z > 0 else "zoom_out" if z < 0 else "stop"

            if pan_dir == "stop" and tilt_dir == "stop" and zoom_dir == "stop":
                self.force_stop()
                return

            if pan_dur > 0 and pan_dir != "stop":
                self._execute_hardware_pulse(pan_dir, pan_dur)
                drift = pan_dur * self.encoder_speed_factor
                self.virtual_pan = max(-1.0, min(1.0, self.virtual_pan + (drift if pan_dir == "right" else -drift)))

            if tilt_dur > 0 and tilt_dir != "stop":
                self._execute_hardware_pulse(tilt_dir, tilt_dur)
                drift = tilt_dur * self.encoder_speed_factor
                self.virtual_tilt = max(-1.0, min(1.0, self.virtual_tilt + (drift if tilt_dir == "up" else -drift)))

            if zoom_dur > 0 and zoom_dir != "stop":
                print(f"[{self.name}] 🔍 Firing relative Zoom pulse ({zoom_dir}) for {zoom_dur:.2f}s")
                self._execute_hardware_pulse(zoom_dir, zoom_dur)

            self.last_move_completion = time.time()

        else:  # CONTINUOUS
            direction = "stop"
            if x > 0: direction = "right"
            elif x < 0: direction = "left"
            elif y > 0: direction = "up"
            elif y < 0: direction = "down"
            elif z > 0: direction = "zoom_in"
            elif z < 0: direction = "zoom_out"
            
            if direction == "stop":
                self.force_stop()
            else:
                with self.lock:
                    self.is_moving = True
                    self.move_end_time = time.time() + 3600
                self._send_http_cmd(direction)

    def _execute_hardware_pulse(self, direction, duration):
        with self.lock:
            self.is_moving = True
            self.move_end_time = time.time() + duration
        self._send_http_cmd(direction)
        time.sleep(duration)
        self._send_http_cmd("stop")
        with self.lock:
            self.is_moving = False

    def _send_http_cmd(self, direction):
        if self.driver == "generic_cgi":
            cmd_id = CMDS.get(direction, 0)
            try:
                self.session.get(self.base_url, params={
                    "command": cmd_id,  
                    "panSpeed": self.pan_velocity,  
                    "tiltSpeed": self.tilt_velocity
                }, timeout=0.5)
            except Exception as e:
                print(f"[{self.name}] ⚠ HTTP Command Failed: {e}")

    def reset_encoder_to_home(self):
        self.virtual_pan = 0.0
        self.virtual_tilt = 0.0
        if self.driver == "generic_cgi":
            try:
                self.session.get(self.preset_url, params={"flag": 4, "existFlag": 1, "presetNum": 0}, timeout=1)
            except Exception as e:
                print(f"[{self.name}] ⚠ Home return failed: {e}")

# ==============================================================================
#  PORT MULTIPLEXER & STRICT ONVIF EMULATOR LAYER   
# ==============================================================================
def create_proxy_app(engine):
    app = Flask(engine.name)

    @app.route('/onvif/device_service', methods=['POST'])
    @app.route('/onvif/media_service', methods=['POST'])
    @app.route('/onvif/ptz_service', methods=['POST'])
    def onvif_handler():
        xml_data = request.data
        if not xml_data: 
            return Response("<soap:Fault/>", 400, mimetype='application/soap+xml')
        try: 
            root = ET.fromstring(xml_data)
        except ET.ParseError: 
            return Response("<soap:Fault/>", 400, mimetype='application/soap+xml')

        body = root.find('.//{http://www.w3.org/2003/05/soap-envelope}Body')
        req_name = "UnknownAction"
        if body is not None and len(body) > 0:
            req_name = body[0].tag.split('}')[-1]
        print(f"📥 [{engine.name}] Inbound SOAP Payload: {req_name}")

        host = request.host
        dev_uri = f"http://{host}/onvif/device_service"
        media_uri = f"http://{host}/onvif/media_service"
        ptz_uri = f"http://{host}/onvif/ptz_service"

        if root.find('.//tds:GetSystemDateAndTime', NAMESPACES) is not None:
            return Response(SOAP_TEMPLATES["GetSystemDateAndTime"], mimetype='application/soap+xml')

        elif root.find('.//trt:GetVideoSources', NAMESPACES) is not None:
            return Response(SOAP_TEMPLATES["GetVideoSources"], mimetype='application/soap+xml')

        elif root.find('.//tds:GetScopes', NAMESPACES) is not None:
            return Response(SOAP_TEMPLATES["GetScopes"], mimetype='application/soap+xml')

        elif root.find('.//tds:GetNetworkInterfaces', NAMESPACES) is not None:
            return Response(SOAP_TEMPLATES["GetNetworkInterfaces"], mimetype='application/soap+xml')

        elif root.find('.//tds:GetDeviceInformation', NAMESPACES) is not None:
            xml_out = SOAP_TEMPLATES["GetDeviceInformation"].replace("CAMERA_NAME", engine.name)
            return Response(xml_out, mimetype='application/soap+xml')

        elif root.find('.//tds:GetCapabilities', NAMESPACES) is not None:
            xml_out = SOAP_TEMPLATES["GetCapabilities"].replace("DEV_URI", dev_uri).replace("MED_URI", media_uri).replace("PTZ_URI", ptz_uri)
            return Response(xml_out, mimetype='application/soap+xml')

        elif root.find('.//trt:GetProfiles', NAMESPACES) is not None:
            return Response(SOAP_TEMPLATES["GetProfiles"], mimetype='application/soap+xml')

        elif root.find('.//trt:GetProfile', NAMESPACES) is not None:
            return Response(SOAP_TEMPLATES["GetProfile"], mimetype='application/soap+xml')

        elif root.find('.//tptz:GetConfigurations', NAMESPACES) is not None:
            return Response(SOAP_TEMPLATES["GetConfigurations"], mimetype='application/soap+xml')

        elif root.find('.//tptz:GetNodes', NAMESPACES) is not None:
            return Response(SOAP_TEMPLATES["GetNodes"], mimetype='application/soap+xml')

        elif root.find('.//tptz:GetConfigurationOptions', NAMESPACES) is not None:
            return Response(SOAP_TEMPLATES["GetConfigurationOptions"], mimetype='application/soap+xml')

        elif root.find('.//tptz:GetServiceCapabilities', NAMESPACES) is not None:
            return Response("""<?xml version="1.0" encoding="utf-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope" xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl">
<SOAP-ENV:Body><tptz:GetServiceCapabilitiesResponse><tptz:Capabilities MoveStatus="true" /></tptz:GetServiceCapabilitiesResponse></SOAP-ENV:Body>
</SOAP-ENV:Envelope>""", mimetype='application/soap+xml')

        elif root.find('.//tptz:RelativeMove', NAMESPACES) is not None:
            pan_tilt_elem = root.find('.//tt:PanTilt', NAMESPACES)
            zoom_elem = root.find('.//tt:Zoom', NAMESPACES)
            x = float(pan_tilt_elem.get('x', 0.0)) if pan_tilt_elem is not None else 0.0
            y = float(pan_tilt_elem.get('y', 0.0)) if pan_tilt_elem is not None else 0.0
            z = float(zoom_elem.get('x', 0.0)) if zoom_elem is not None else 0.0
            engine.dispatch_move("RELATIVE", x, y, z)
            return Response("""<?xml version="1.0" encoding="utf-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope" xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl">
<SOAP-ENV:Body><tptz:RelativeMoveResponse/></SOAP-ENV:Body></SOAP-ENV:Envelope>""", mimetype='application/soap+xml')

        elif root.find('.//tptz:ContinuousMove', NAMESPACES) is not None:
            pan_tilt_elem = root.find('.//tt:PanTilt', NAMESPACES)
            zoom_elem = root.find('.//tt:Zoom', NAMESPACES)
            x = float(pan_tilt_elem.get('x', 0.0)) if pan_tilt_elem is not None else 0.0
            y = float(pan_tilt_elem.get('y', 0.0)) if pan_tilt_elem is not None else 0.0
            z = float(zoom_elem.get('x', 0.0)) if zoom_elem is not None else 0.0
            engine.dispatch_move("CONTINUOUS", x, y, z)
            return Response("""<?xml version="1.0" encoding="utf-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope" xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl">
<SOAP-ENV:Body><tptz:ContinuousMoveResponse/></SOAP-ENV:Body></SOAP-ENV:Envelope>""", mimetype='application/soap+xml')

        elif root.find('.//tptz:GetStatus', NAMESPACES) is not None:
            current_status, virt_p, virt_t = engine.get_engine_status()
            return Response(f"""<?xml version="1.0" encoding="utf-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope" xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema">
<SOAP-ENV:Body><tptz:GetStatusResponse><tptz:PTZStatus>
<tt:Position>
<tt:PanTilt x="{virt_p:.4f}" y="{virt_t:.4f}" space="http://www.onvif.org/ver10/tptz/PanTiltSpaces/PositionGenericSpace"/>
<tt:Zoom x="0.0" space="http://www.onvif.org/ver10/tptz/ZoomSpaces/PositionGenericSpace"/>
</tt:Position>
<tt:MoveStatus><tt:PanTilt>{current_status}</tt:PanTilt><tt:Zoom>IDLE</tt:Zoom></tt:MoveStatus>
</tptz:PTZStatus></tptz:GetStatusResponse></SOAP-ENV:Body></SOAP-ENV:Envelope>""", mimetype='application/soap+xml')

        elif root.find('.//tptz:Stop', NAMESPACES) is not None:
            engine.force_stop()
            return Response("""<?xml version="1.0" encoding="utf-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope" xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl">
<SOAP-ENV:Body><tptz:StopResponse/></SOAP-ENV:Body></SOAP-ENV:Envelope>""", mimetype='application/soap+xml')

        elif root.find('.//tptz:GetPresets', NAMESPACES) is not None:
            return Response("""<?xml version="1.0" encoding="utf-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope" xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema">
<SOAP-ENV:Body><tptz:GetPresetsResponse><tptz:Preset token="preset_home"><tt:Name>home</tt:Name></tptz:Preset></tptz:GetPresetsResponse></SOAP-ENV:Body>
</SOAP-ENV:Envelope>""", mimetype='application/soap+xml')

        elif root.find('.//tptz:GotoPreset', NAMESPACES) is not None:
            engine.reset_encoder_to_home()
            return Response("""<?xml version="1.0" encoding="utf-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope" xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl">
<SOAP-ENV:Body><tptz:GotoPresetResponse/></SOAP-ENV:Body></SOAP-ENV:Envelope>""", mimetype='application/soap+xml')

        else:
            print(f"ℹ [{engine.name}] Sub-Discovery Stub Activated for: {req_name}")
            return Response("""<?xml version="1.0" encoding="utf-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope">
<SOAP-ENV:Body><SOAP-ENV:Fault><SOAP-ENV:Code><SOAP-ENV:Value>SOAP-ENV:Sender</SOAP-ENV:Value></SOAP-ENV:Code>
<SOAP-ENV:Reason><SOAP-ENV:Text xml:lang="en">Stubbed Out By Proxy</SOAP-ENV:Text></SOAP-ENV:Reason></SOAP-ENV:Fault></SOAP-ENV:Body>
</SOAP-ENV:Envelope>""", mimetype='application/soap+xml')
            
    return app

class ProxyServerThread(threading.Thread):
    def __init__(self, engine, port):
        super().__init__(daemon=True)
        self.engine = engine
        self.port = port
        self.app = create_proxy_app(engine)
        self.server = make_server('0.0.0.0', self.port, self.app)

    def run(self):
        print(f"🚀 Started Native Proxy for '{self.engine.name}' directly on host port {self.port}")
        self.server.serve_forever()

if __name__ == '__main__':
    with open('config.yaml', 'r') as f:
        config_data = yaml.safe_load(f)

    servers = []
    for cam_name, cam_cfg in config_data.get('cameras', {}).items():
        password = os.environ.get(cam_cfg.get('password_env', ''), 'admin')  
        port = cam_cfg.get('port', 8080)
        
        engine = MotionEngine(cam_name, cam_cfg, password)
        engine.start()
        
        server_thread = ProxyServerThread(engine, port)
        server_thread.start()
        servers.append(server_thread)
        
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n👋 Native Proxy Server shutting down gracefully...")
