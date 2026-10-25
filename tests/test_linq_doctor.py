"""Диагностика подключения к LinQ: что не настроено, и что значения доступов не печатаются."""

from linq import config, doctor


def settings(**env):
    base = {
        "OVPN_PROFILE": "C:/x/hackathon.ovpn",
        "OVPN_USER": "u",
        "OVPN_PASS": "SECRET-PASSWORD",
        "JUPYTER_LOGIN": "hackathon1",
        "JUPYTER_PASS": "SECRET-JUPYTER",
    }
    base.update(env)
    return config.load(base, env_file=config.ROOT / "no-such.env")


def run(s, **over):
    args = dict(
        find_exe=lambda _x: "C:/Program Files/OpenVPN/bin/openvpn.exe",
        profile_exists=lambda _p: True,
        has_module=lambda _n: True,
        has_ml_module=lambda _n: True,
        has_task=lambda _t: True,
        adapters=lambda: [],
        stand_visible=lambda _h: True,
        windows=True,
    )
    args.update(over)
    return doctor.evaluate(s, **args)


def test_everything_ready():
    checks = run(settings())
    assert all(c.ok for c in checks)
    assert "Всё готово" in doctor.render(checks)


def test_reports_each_missing_piece_with_a_fix():
    checks = run(
        settings(OVPN_PASS="", JUPYTER_PASS=""),
        find_exe=lambda _x: "",
        profile_exists=lambda _p: False,
        has_module=lambda n: n != "websocket",
        has_task=lambda _t: False,
        stand_visible=lambda _h: False,
    )
    failed = {c.name for c in checks if not c.ok}
    assert {
        "OpenVPN установлен",
        "Профиль хакатона (.ovpn)",
        "Поля доступа заполнены",
        "websocket-client (терминал стенда)",
        "Задачи без запроса UAC",
    } <= failed
    text = doctor.render(checks)
    assert "OVPN_PASS" in text and "JUPYTER_PASS или JUPYTER_TOKEN" in text
    assert "linq-auto-setup.bat" in text and "Не готово" in text
    assert doctor.main.__name__ == "main"


def test_stand_and_pika_are_not_required():
    checks = run(settings(), has_ml_module=lambda n: n != "pika", stand_visible=lambda _h: False)
    text = doctor.render(checks)
    assert "Всё готово" in text  # туннель поднимет linq-up, pika нужен только для живого потока
    assert all(not c.required for c in checks if not c.ok)


def test_secret_values_are_never_printed():
    text = doctor.render(run(settings(), find_exe=lambda _x: ""))
    assert "SECRET-PASSWORD" not in text and "SECRET-JUPYTER" not in text


def test_neighbor_vpn_is_named_but_not_a_failure():
    adapters = [
        {"Name": "AmneziaVPN", "Status": "Up", "InterfaceDescription": "WireGuard Tunnel"},
        {"Name": "Ethernet", "Status": "Up", "InterfaceDescription": "Intel Ethernet"},
        {"Name": "OpenVPN TAP", "Status": "Up", "InterfaceDescription": "TAP-Windows Adapter V9"},
    ]
    assert doctor.neighbor_vpns(adapters) == ["AmneziaVPN (Up)"]
    checks = run(settings(), adapters=lambda: adapters)
    neighbor = next(c for c in checks if c.name.startswith("Соседний"))
    assert neighbor.ok and "AmneziaVPN" in neighbor.detail


def test_non_windows_skips_windows_only_checks():
    names = {c.name for c in run(settings(), windows=False)}
    assert "Задачи без запроса UAC" not in names
