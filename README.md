# oxi-mcp

MCP server for the **OXI ONE MKII**: drive it over USB MIDI, take over its pads/LEDs/OLED with the
SysEx remote mode, and read/edit my projects stored in [rack-library](../rack-library) (`projects/<slug>/`).

```
pip install -e .
claude mcp add --scope user oxi-one -- python -m oxi_mcp.server
python -m unittest            # protocol bytes vs OXI's own examples + tools through a fake port
```

Plug the OXI in over USB (device mode, manual p.261). On Windows only one app can hold a MIDI port at a time,
so close anything else that has it open.

## Tools

| area | tools | basis |
|---|---|---|
| connection | `oxi_status` (ports, firmware probe), `oxi_connect`, `oxi_disconnect` | |
| plain MIDI | `oxi_transport`, `oxi_clock` (24 ppqn, background thread), `oxi_select_column` (Program Change), `oxi_send_note`, `oxi_send_cc` | manual: sync p.265, PC column select p.240, transpose p.68, capture p.93 |
| remote mode | `oxi_remote`, `oxi_grid`, `oxi_show_pattern`, `oxi_led`, `oxi_led_names`, `oxi_oled`, `oxi_events` | OXI **Remote Control Reference** sheet, MKII tab (linked from manual p.273) |
| projects | `oxi_projects`, `oxi_pattern`, `oxi_edit_pattern` (commits via rack-library's save path), `oxi_export` (to SD card) | rack-library `tools/oxi.py` |
| raw | `oxi_sysex` (refuses fw-update / mem-reset / project send+delete unless `allow_destructive`), `oxi_probe_project` | **experimental**: MK1 OXI-Desktop protocol |

## Protocol notes

- Header MKII `F0 00 21 5B 01 01` (MK1 `.. 00 01`). Values after the category byte are nibbled (hi, lo).
- Remote: enter `06 55` -> ACK `06 53`; exit `00`; pad `01 id r g b`; button LED `02 id value`; OLED `03` + 1024 bytes.
- Pad id = x + 16*y with 16 columns x 8 rows. The sheet's text says x 0-7 / y 0-15, but its "row 2 col 4"
  example (id 0x13) only fits 16x8; the tests encode both of its examples byte for byte.
- OLED pixel packing is not documented. `oxi_oled(layout="pages")` is SSD1306-style; try `"rows"` if scrambled.
- Events in remote mode: pads = notes on ch 1 (x + 16y), buttons = notes on ch 2 (FunctionButtons_e order),
  encoders = CC on ch 1, value 1-8 clockwise / 0x11-0x18 counter-clockwise.
- SYSTEM version (`03 00`) and PROJECT (`05 ..`) requests come from the MK1 desktop app; unverified on MKII.

## Prior art

- OXI Remote Control Reference (official sheet): https://docs.google.com/spreadsheets/d/1Yccnrluv10QL_PauMmtCt64EtYjfSeZEXKrlrw8P24w
- faqteur/OXI-Desktop (MK1 desktop app, GPL): SysEx categories, project/pattern transfer.
- wylie39/oxi-one-web: TypeScript port of the MK1 transfer (on hold).
- OXI-Instruments/oxi_midi_import: MIDI file -> pattern models (no SysEx).
- thorinside/bitwig_oxi_one_kotlin: Bitwig remote extension.
- Ziforge/cirklon-mcp: the closest MCP (Sequentix Cirklon; FastMCP + python-rtmidi); this follows its shape.
