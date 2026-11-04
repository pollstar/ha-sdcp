"""Protocol helpers."""
from custom_components.sdcp import protocol as p


def test_parse_discovery_v3():
    raw = b'{"Id":"x","Data":{"Name":"SATURN","MachineName":"Saturn 4 Ultra 16K","BrandName":"ELEGOO","MainboardIP":"192.168.0.97","MainboardID":"c4","ProtocolVersion":"V3.0.0","FirmwareVersion":"V1.2.3"}}'
    d = p.parse_discovery(raw, "192.168.0.97")
    assert d.host == "192.168.0.97" and d.mainboard_id == "c4" and "Saturn 4 Ultra 16K" in d.label


def test_parse_discovery_nested_and_garbage():
    d = p.parse_discovery(b'{"Data":{"Attributes":{"MainboardID":"z","Name":"N"}}}', "10.0.0.5")
    assert d.host == "10.0.0.5" and d.mainboard_id == "z"
    assert p.parse_discovery(b"M99999", "1.1.1.1") is None


def test_status_helpers():
    assert p.machine_status_code({"CurrentStatus": [1]}) == 1
    assert p.machine_status_code({"CurrentStatus": 2}) == 2
    assert p.print_status_name({"PrintInfo": {"Status": 8}}) == "stopped"
    assert p.print_status_name({"PrintInfo": {"Status": 99}}) == "unknown"
    assert p.ticks_scale({"TempOfTank": 1}) == 0.001
    assert p.ticks_scale({"TempOfNozzle": 200}) == 1.0


def test_split_host():
    assert p.split_host("192.168.0.97") == ("192.168.0.97", 3030)
    assert p.split_host("127.0.0.1:4000") == ("127.0.0.1", 4000)
    assert p.split_host("[fe80::1]:81") == ("fe80::1", 81)



def test_printer_resolution():
    from custom_components.sdcp.printers import resolve
    assert resolve({"MachineName": "Saturn 4 Ultra 16K"}, {"TempOfTank": 1}).profile == "saturn-4-ultra"
    assert resolve({"MachineName": "Saturn  4 ultra"}, {}).id == "elegoo-saturn-4-ultra"
    assert resolve({"MachineName": "Mars 5 Ultra"}, {"TempOfUVLED": 30}).id == "generic-resin"
    assert resolve({"MachineName": "Some FDM"}, {"TempOfNozzle": 200}).id == "generic-fdm"
    assert resolve({"MachineName": "Centauri Carbon"}, {"TempOfNozzle": 200}).tick_seconds == 1.0
