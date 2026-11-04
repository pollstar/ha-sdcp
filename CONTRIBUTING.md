# Contributing: adding a printer

The integration is written for **any** printer that speaks SDCP v3 (Chitu boards:
ELEGOO, and other brands using the same firmware family). Everything that differs between
machines is described in one place — a **printer model** in
`custom_components/sdcp/printers/` — so adding a printer normally means adding one small
module and one test fixture, without touching the core.

```
custom_components/sdcp/
├── protocol.py        SDCP transport: discovery, WebSocket client, commands (no HA imports)
├── coordinator.py     one connection per printer, events, job details, camera URL
├── descriptions.py    entity description types and helpers for printer modules
├── printers/
│   ├── __init__.py    PrinterModel, CameraProfile, register(), resolve(), MODULES
│   ├── generic.py     generic resin / generic FDM fallbacks
│   └── elegoo.py      Saturn 4 Ultra (16K), Centauri Carbon
├── sensor.py, binary_sensor.py, button.py, switch.py, camera.py, image.py
└── translations/      en, fr, de, es, uk, ru
tests/
├── fixtures/          one JSON capture per printer (Attributes + Status + expectations)
├── printer_sim.py     SDCP emulator that replays a fixture
└── test_models.py     runs every fixture through Home Assistant
scripts/capture_fixture.py
```

## 1. Capture your printer

```bash
pip install aiohttp
python scripts/capture_fixture.py <printer-ip> tests/fixtures/<brand>_<model>.json
```

The script sends only the read-only commands 0 (status) and 1 (attributes). Capture while
the printer is **printing** if you can — that is where the differences show (stage codes,
timer units). The IP and MainboardID are replaced with placeholders.

Fill in the `expect` block of the fixture:

| Key | Meaning |
|---|---|
| `model` | id of the `PrinterModel` that must match |
| `profile` | drawing of the card (`printer_profile` attribute) |
| `kind` | `resin` or `fdm` |
| `print_status` | stage name the capture shows |
| `remaining_min` | minutes left, as the printer's own screen shows it — this checks the timer unit |
| `sensors` / `absent` | sensor keys that must / must not be created |
| `camera` | whether a camera entity must exist |

Run `pytest`. With no model of its own the capture falls back to `generic-resin` /
`generic-fdm`; if that already gives the right result, the fixture alone is a useful
contribution.

## 2. Describe the model

Create `printers/<brand>.py` (or add to an existing one) and list the module in
`MODULES` in `printers/__init__.py`:

```python
from ..protocol import PRINT_STATUS
from . import CameraProfile, PrinterModel, extend, name_contains, register

MY_PRINTER = register(PrinterModel(
    id="acme-resin-9000",                 # stable, used in diagnostics and tests
    name="ACME Resin 9000",
    kind="resin",                         # "resin" | "fdm"
    match=name_contains("resin 9000", brand="ACME"),   # Attributes.MachineName / BrandName
    priority=10,                          # beats the generic models
    card_profile="generic-resin",         # drawing in pollstar/sdcp-printer-card
    tick_seconds=0.001,                   # PrintInfo.*Ticks unit: 0.001 = ms, 1.0 = s
    print_status=extend(PRINT_STATUS, **{"14": "printing"}),   # stage codes that differ
    camera=CameraProfile(rtsp_transport="tcp", fallback_url="rtsp://{host}:554/live"),
))
```

| Field | Default | Use it when |
|---|---|---|
| `match` / `priority` | never / 0 | always; `match(attributes, status)` may look at any field |
| `tick_seconds` | 0.001 | the remaining time is 1000× off |
| `machine_status`, `print_status`, `print_errors` | SDCP v3 tables | codes mean something else or there are extra ones; use `extend()` |
| `active_stages`, `paused_stages` | SDCP v3 sets | a new stage name means printing / paused |
| `health_keys` | known `DevicesStatus` keys | the self-test has new keys (unknown keys still get a sensor named after the key) |
| `camera` | RTSP over UDP on :554, re-enable every 10 min | the stream URL or transport differs |
| `sensors`, `binary_sensors`, `buttons` | none | the printer reports data or accepts commands the core does not know |
| `card_profile` | `generic-<kind>` | a drawing for this printer exists in the card repository |

Every core sensor is created only when the printer reports its key, so most printers need
nothing beyond `match`, `tick_seconds` and maybe a few codes.

### Extra entities

Entity descriptions come from `descriptions.py`. `value_fn` / `exists_fn` receive the
coordinator: raw SDCP dicts are in `c.status`, `c.attributes`, `c.print_info`; the client
is `c.client` (`await c.client.request(cmd, data)` sends any command and raises
`SDCPCommandError` on a non-zero `Ack`).

```python
from ..descriptions import SDCPButtonDescription, SDCPSensorDescription, has_status, status_value

sensors=(
    SDCPSensorDescription(key="resin_level", translation_key="resin_level",
                          native_unit_of_measurement="%", value_fn=status_value("ResinLevel"),
                          exists_fn=has_status("ResinLevel")),
),
buttons=(
    SDCPButtonDescription(key="light_toggle", translation_key="light_toggle",
                          press_fn=lambda client: client.request(403, {"LightStatus": {"SecondLight": 1}})),
),
```

Unique ids are `<MainboardID>_<key>`, so keep keys stable once released.

## 3. Translations

Add the `translation_key` of every new entity (and new enum states) to
`strings.json` and to all files in `translations/` (en, fr, de, es, uk, ru). English is
enough to start; untranslated languages fall back to English.

## 4. The card

The Lovelace card ([pollstar/sdcp-printer-card](https://github.com/pollstar/sdcp-printer-card))
loads the drawing named by `card_profile`. Generic drawings work for any resin / FDM
printer; to give a printer its own look, add `dist/sdcp-printer-<profile>.js` there
(see that repository's README) and set `card_profile` here.

## 5. Checks

```bash
pip install -r requirements_test.txt
pytest                       # includes one Home Assistant run per fixture
python tests/printer_sim.py 3030   # emulator for manual testing (Saturn 4 Ultra data)
```

Please do not send commands that change the printer (print, delete, rename, camera) from
tests against real hardware. On some firmwares toggling the camera (Cmd 386) quickly
crashes the printer — the integration only ever enables it, at most once per
`CameraProfile.reenable_after`.
