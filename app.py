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

# Silence Flask HTTP logging
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

CMDS = {"stop": 0, "up": 1, "down": 2, "left": 3, "right": 4}

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
                move_type, x, y = self.work_queue.get(timeout=0.1)
                self._process_movement(move_type, x, y)
                self.work_queue.task_done()
            except queue.Empty:
                with self.lock:
                    if self.is_moving and time.time() >= self.move_end_time:
                        self.is_moving = False

    def dispatch_move(self, move_type, x, y):
        self.work_queue.put((move_type, x, y))

    def force_stop(self):
        while not self.work_queue.empty():
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

    def _process_movement(self, move_type, x, y):
        current_time = time.time()
        print(f"[{self.name}] 📊 Vector: x={x:.3f} y={y:.3f} ({move_type})")

        if move_type == "RELATIVE":
            if (current_time - self.last_move_completion) < self.cooldown_window:
                return 

            pan_dur = max(0.10, min(0.45, abs(x) * self.scale_multiplier)) if abs(x) > 0.05 else 0.0
            tilt_dur = max(0.10, min(0.45, abs(y) * self.scale_multiplier)) if abs(y) > 0.05 else 0.0
            pan_dir = "right" if x > 0 else "left" if x < 0 else "stop"
            tilt_dir = "up" if y > 0 else "down" if y < 0 else "stop"

            if pan_dir == "stop" and tilt_dir == "stop":
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

            self.last_move_completion = time.time()

        else: 
            direction = "stop"
            if x > 0: direction = "right"
            elif x < 0: direction = "left"
            elif y > 0: direction = "up"
            elif y < 0: direction = "down"
            
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
#  DYNAMIC PORT MULTIPLEXER 
# ==============================================================================
def create_proxy_app(engine):
    app = Flask(engine.name)
    
    @app.route('/onvif/device_service', methods=['POST'])
    @app.route('/onvif/ptz_service', methods=['POST'])
    def onvif_handler():
        xml_data = request.data
        if not xml_data: return Response("<soap:Fault/>", mimetype='application/soap+xml'), 400
        try: root = ET.fromstring(xml_data)
        except ET.ParseError: return Response("<soap:Fault/>", mimetype='application/soap+xml'), 400

        host = request.host
        dev_uri = f"http://{host}/onvif/device_service"
        ptz_uri = f"http://{host}/onvif/ptz_service"

        # 1. GetDeviceInformation (The missing handshake Frigate requires!)
        if root.find('.//tds:GetDeviceInformation', NAMESPACES) is not None:
            return Response("""<?xml version="1.0" encoding="utf-8"?><SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope" xmlns:tds="http://www.onvif.org/ver10/device/wsdl"><SOAP-ENV:Body><tds:GetDeviceInformationResponse><tds:Manufacturer>CustomProxy</tds:Manufacturer><tds:Model>V4Engine</tds:Model><tds:FirmwareVersion>1.0</tds:FirmwareVersion><tds:SerialNumber>12345</tds:SerialNumber><tds:HardwareId>1.0</tds:HardwareId></tds:GetDeviceInformationResponse></SOAP-ENV:Body></SOAP-ENV:Envelope>""", mimetype='application/soap+xml')

        elif root.find('.//tds:GetCapabilities', NAMESPACES) is not None:
            return Response(f"""<?xml version="1.0" encoding="utf-8"?><SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope" xmlns:tds="http://www.onvif.org/ver10/device/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema"><SOAP-ENV:Body><tds:GetCapabilitiesResponse><tds:Capabilities><tt:Device><tt:XAddr>{dev_uri}</tt:XAddr></tt:Device><tt:Media><tt:XAddr>{dev_uri}</tt:XAddr></tt:Media><tt:PTZ><tt:XAddr>{ptz_uri}</tt:XAddr></tt:PTZ></tds:Capabilities></tds:GetCapabilitiesResponse></SOAP-ENV:Body></SOAP-ENV:Envelope>""", mimetype='application/soap+xml')

        elif root.find('.//trt:GetProfiles', NAMESPACES) is not None:
            return Response("""<?xml version="1.0" encoding="utf-8"?><SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope" xmlns:trt="http://www.onvif.org/ver10/media/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema"><SOAP-ENV:Body><trt:GetProfilesResponse><trt:Profiles token="Profile_1"><tt:Name>MainProfile</tt:Name><tt:VideoEncoderConfiguration token="VideoEncoder_1"><tt:Name>VideoConfig</tt:Name><tt:UseCount>1</tt:UseCount><tt:Encoding>H264</tt:Encoding><tt:Resolution><tt:Width>1280</tt:Width><tt:Height>960</tt:Height></tt:Resolution></tt:VideoEncoderConfiguration><tt:PTZConfiguration token="PTZ_1"><tt:Name>PTZConfig</tt:Name><tt:DefaultRelativePanTiltTranslationSpace>http://www.onvif.org/ver10/tptz/PanTiltSpaces/TranslationSpaceFov</tt:DefaultRelativePanTiltTranslationSpace><tt:DefaultContinuousPanTiltVelocitySpace>http://www.onvif.org/ver10/tptz/PanTiltSpaces/VelocitySpaceGeneric</tt:DefaultContinuousPanTiltVelocitySpace></tt:PTZConfiguration></trt:Profiles></trt:GetProfilesResponse></SOAP-ENV:Body></SOAP-ENV:Envelope>""", mimetype='application/soap+xml')

        elif root.find('.//tptz:GetConfigurationOptions', NAMESPACES) is not None:
            return Response("""<?xml version="1.0" encoding="utf-8"?><SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope" xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema"><SOAP-ENV:Body><tptz:GetConfigurationOptionsResponse><tptz:PTZConfigurationOptions><tt:Spaces><tt:RelativePanTiltTranslationSpace><tt:URI>http://www.onvif.org/ver10/tptz/PanTiltSpaces/TranslationSpaceFov</tt:URI><tt:XRange><tt:Min>-1</tt:Min><tt:Max>1</tt:Max></tt:XRange><tt:YRange><tt:Min>-1</tt:Min><tt:Max>1</tt:Max></tt:YRange></tt:RelativePanTiltTranslationSpace></tt:Spaces></tptz:PTZConfigurationOptions></tptz:GetConfigurationOptionsResponse></SOAP-ENV:Body></SOAP-ENV:Envelope>""", mimetype='application/soap+xml')

        elif root.find('.//tptz:GetServiceCapabilities', NAMESPACES) is not None:
            return Response("""<?xml version="1.0" encoding="utf-8"?><SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope" xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl"><SOAP-ENV:Body><tptz:GetServiceCapabilitiesResponse><tptz:Capabilities MoveStatus="true" /></tptz:GetServiceCapabilitiesResponse></SOAP-ENV:Body></SOAP-ENV:Envelope>""", mimetype='application/soap+xml')

        elif root.find('.//tptz:RelativeMove', NAMESPACES) is not None:
            pan_tilt_elem = root.find('.//tt:PanTilt', NAMESPACES)
            if pan_tilt_elem is not None:
                x = float(pan_tilt_elem.get('x', 0.0))
                y = float(pan_tilt_elem.get('y', 0.0))
                engine.dispatch_move("RELATIVE", x, y)
            return Response("""<?xml version="1.0" encoding="utf-8"?><SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope" xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl"><SOAP-ENV:Body><tptz:RelativeMoveResponse/></SOAP-ENV:Body></SOAP-ENV:Envelope>""", mimetype='application/soap+xml')

        elif root.find('.//tptz:ContinuousMove', NAMESPACES) is not None:
            pan_tilt_elem = root.find('.//tt:PanTilt', NAMESPACES)
            if pan_tilt_elem is not None:
                x = float(pan_tilt_elem.get('x', 0.0))
                y = float(pan_tilt_elem.get('y', 0.0))
                engine.dispatch_move("CONTINUOUS", x, y)
            return Response("""<?xml version="1.0" encoding="utf-8"?><SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope" xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl"><SOAP-ENV:Body><tptz:ContinuousMoveResponse/></SOAP-ENV:Body></SOAP-ENV:Envelope>""", mimetype='application/soap+xml')

        elif root.find('.//tptz:GetStatus', NAMESPACES) is not None:
            current_status, virt_p, virt_t = engine.get_engine_status()
            return Response(f"""<?xml version="1.0" encoding="utf-8"?><SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope" xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema"><SOAP-ENV:Body><tptz:GetStatusResponse><tptz:PTZStatus><tt:Position><tt:PanTilt x="{virt_p:.4f}" y="{virt_t:.4f}" space="http://www.onvif.org/ver10/tptz/PanTiltSpaces/PositionGenericSpace"/><tt:Zoom x="0.0" space="http://www.onvif.org/ver10/tptz/ZoomSpaces/PositionGenericSpace"/></tt:Position><tt:MoveStatus><tt:PanTilt>{current_status}</tt:PanTilt><tt:Zoom>IDLE</tt:Zoom></tt:MoveStatus></tptz:PTZStatus></tptz:GetStatusResponse></SOAP-ENV:Body></SOAP-ENV:Envelope>""", mimetype='application/soap+xml')

        elif root.find('.//tptz:Stop', NAMESPACES) is not None:
            engine.force_stop()
            return Response("""<?xml version="1.0" encoding="utf-8"?><SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope" xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl"><SOAP-ENV:Body><tptz:StopResponse/></SOAP-ENV:Body></SOAP-ENV:Envelope>""", mimetype='application/soap+xml')

        elif root.find('.//tptz:GetPresets', NAMESPACES) is not None:
            return Response("""<?xml version="1.0" encoding="utf-8"?><SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope" xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema"><SOAP-ENV:Body><tptz:GetPresetsResponse><tptz:Preset token="preset_home"><tt:Name>home</tt:Name></tptz:Preset></tptz:GetPresetsResponse></SOAP-ENV:Body></SOAP-ENV:Envelope>""", mimetype='application/soap+xml')

        elif root.find('.//tptz:GotoPreset', NAMESPACES) is not None:
            engine.reset_encoder_to_home()
            return Response("""<?xml version="1.0" encoding="utf-8"?><SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope" xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl"><SOAP-ENV:Body><tptz:GotoPresetResponse/></SOAP-ENV:Body></SOAP-ENV:Envelope>""", mimetype='application/soap+xml')

        else:
            # Replaced <ack/> with a proper, strict SOAP Fault envelope for unknown requests
            return Response("""<?xml version="1.0" encoding="utf-8"?><SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope"><SOAP-ENV:Body><SOAP-ENV:Fault><SOAP-ENV:Code><SOAP-ENV:Value>SOAP-ENV:Sender</SOAP-ENV:Value></SOAP-ENV:Code><SOAP-ENV:Reason><SOAP-ENV:Text xml:lang="en">Not Implemented</SOAP-ENV:Text></SOAP-ENV:Reason></SOAP-ENV:Fault></SOAP-ENV:Body></SOAP-ENV:Envelope>""", mimetype='application/soap+xml')
            
    return app
class ProxyServerThread(threading.Thread):
    def __init__(self, engine, port):
        super().__init__(daemon=True)
        self.engine = engine
        self.port = port
        self.app = create_proxy_app(engine)
        self.server = make_server('0.0.0.0', self.port, self.app)

    def run(self):
        print(f"🚀 Started Proxy for '{self.engine.name}' on port {self.port}")
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
        print("Shutting down...")
