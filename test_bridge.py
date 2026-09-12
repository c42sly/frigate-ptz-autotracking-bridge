"""Self-contained tests for the ONVIF driver addition. No real camera required."""
import time
import xml.etree.ElementTree as ET
import requests
import app as bridge


def make_engine(driver, **over):
    cfg = {"ip": "10.0.10.231", "user": "admin", "driver": driver, "port": 9998}
    cfg.update(over)
    return bridge.MotionEngine("test", cfg, "secretpass")


def test_onvif_soap():
    eng = make_engine("onvif", profile_token="MainStream",
                      onvif_pan_velocity=0.5, onvif_tilt_velocity=0.5, home_preset_token="7")
    posts = []
    eng._onvif_post = lambda action, body: posts.append((action, body))
    for d in ("left", "right", "up", "down", "stop"):
        eng._send_cmd(d)
    eng.reset_encoder_to_home()

    # every emitted body must be well-formed standalone XML
    for action, body in posts:
        ET.fromstring(body)

    assert posts[0][0].endswith("/ContinuousMove") and 'x="-0.5"' in posts[0][1]
    assert 'x="0.5"' in posts[1][1]
    assert 'y="0.5"' in posts[2][1]
    assert 'y="-0.5"' in posts[3][1]
    assert posts[4][0].endswith("/Stop")
    assert posts[5][0].endswith("/GotoPreset") and "<PresetToken>7</PresetToken>" in posts[5][1]

    # WS-Security digest header is well-formed and secret is never sent in clear
    hdr = eng._ws_security()
    ET.fromstring(hdr)
    assert "PasswordDigest" in hdr and "<wsu:Created>" in hdr and "<wsse:Nonce" in hdr
    assert "secretpass" not in hdr
    print("TEST1 onvif SOAP correctness: PASS")


def test_cgi_regression():
    eng = make_engine("generic_cgi", pan_velocity=7, tilt_velocity=6)
    calls = []

    class FakeSession:
        auth = None
        def get(self, url, params=None, timeout=None):
            calls.append((url, params))

    eng.session = FakeSession()
    eng._send_cmd("left")
    url, params = calls[0]
    assert url.endswith("/form/setPTZCfg"), url
    assert params["command"] == 3 and params["panSpeed"] == 7 and params["tiltSpeed"] == 6, params
    # default driver (unset) must still behave as generic_cgi
    eng2 = bridge.MotionEngine("d", {"ip": "1.2.3.4", "user": "admin"}, "p")
    assert eng2.driver == "generic_cgi"
    print("TEST2 generic_cgi regression: PASS")


def test_flask_handshake_and_dispatch():
    eng = make_engine("onvif", profile_token="MainStream")
    posts = []
    eng._onvif_post = lambda a, b: posts.append((a, b))
    eng.start()
    srv = bridge.ProxyServerThread(eng, 9998)
    srv.start()
    time.sleep(0.6)
    base = "http://127.0.0.1:9998/onvif/ptz_service"

    def soap(inner, extra_ns=""):
        return (f'<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" {extra_ns}>'
                f'<s:Body>{inner}</s:Body></s:Envelope>')

    gp = requests.post(base, data=soap('<GetProfiles xmlns="http://www.onvif.org/ver10/media/wsdl"/>'),
                       headers={"Content-Type": "application/soap+xml"}, timeout=3)
    assert "TranslationSpaceFov" in gp.text, "GetProfiles must advertise FOV space for Frigate"

    co = requests.post(base, data=soap('<GetConfigurationOptions xmlns="http://www.onvif.org/ver20/ptz/wsdl"/>'),
                       headers={"Content-Type": "application/soap+xml"}, timeout=3)
    assert "TranslationSpaceFov" in co.text, "GetConfigurationOptions must advertise FOV space"

    rm = ('<RelativeMove xmlns="http://www.onvif.org/ver20/ptz/wsdl"><ProfileToken>x</ProfileToken>'
          '<Translation><tt:PanTilt x="0.3" y="0.0"/></Translation></RelativeMove>')
    r = requests.post(base, data=soap(rm, 'xmlns:tt="http://www.onvif.org/ver10/schema"'),
                      headers={"Content-Type": "application/soap+xml"}, timeout=3)
    assert "RelativeMoveResponse" in r.text
    time.sleep(1.0)  # let the engine thread run the pulse
    actions = [a.rsplit("/", 1)[-1] for a, b in posts]
    assert "ContinuousMove" in actions and "Stop" in actions, f"pulse not dispatched: {actions}"
    print("TEST3 Flask handshake + relative-move dispatch: PASS", actions)


if __name__ == "__main__":
    test_onvif_soap()
    test_cgi_regression()
    test_flask_handshake_and_dispatch()
    print("\nALL TESTS PASSED")
