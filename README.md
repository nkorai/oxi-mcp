# oxi-mcp

An [MCP](https://modelcontextprotocol.io) server for the **OXI ONE MKII** sequencer. It lets an AI assistant
(Claude Code, Claude Desktop, or any MCP client) drive the OXI over USB MIDI, and take over its pads,
button LEDs and OLED screen through the device's SysEx remote mode.

Unofficial; not affiliated with OXI Instruments.

> **Status: early.** The protocol bytes are tested against the examples in OXI's own reference sheet,
> and every tool is tested through a fake MIDI port. Hardware testing is in progress, so expect fixes:
> see [Not yet verified on hardware](#not-yet-verified-on-hardware).

## Install

```
git clone https://github.com/nkorai/oxi-mcp && cd oxi-mcp
pip install -e .
claude mcp add --scope user oxi-one -- python -m oxi_mcp.server      # Claude Code
python -m unittest                                                    # tests, no device needed
```

Claude Desktop / other clients: run `python -m oxi_mcp.server` (stdio) or the `oxi-mcp` script.

Connect the OXI over USB in device mode (manual p.261). On Windows only one app can hold a MIDI port at a time,
so close anything else that has the OXI open. `oxi_connect(port_hint=...)` picks other ports, e.g. a loopMIDI
port for testing without the device.

## Tools

| area | tools | basis |
|---|---|---|
| connection | `oxi_status` (ports, firmware probe), `oxi_connect`, `oxi_disconnect` | |
| plain MIDI | `oxi_transport`, `oxi_clock` (24 ppqn from a background thread), `oxi_select_column` (Program Change), `oxi_send_note`, `oxi_send_cc` | MKII manual: sync p.265, PC column select p.240, transpose p.68, MIDI capture p.93 |
| remote mode | `oxi_remote`, `oxi_grid`, `oxi_led`, `oxi_led_names`, `oxi_oled` (text, with PNG preview), `oxi_events` (pads, buttons, encoders) | OXI **Remote Control Reference** sheet, MKII tab (linked from manual p.273) |
| raw | `oxi_sysex` (refuses firmware-update / memory-reset / project send+delete unless `allow_destructive=True`), `oxi_probe_project` | **experimental**: MK1 desktop-app protocol |
| projects (optional) | `oxi_projects`, `oxi_pattern`, `oxi_show_pattern` (draw a pattern on the pads), `oxi_edit_pattern` | needs `RACK_LIBRARY`, below |
| SD card | `oxi_card` (what's on the card vs the library), `oxi_card_pull` (card -> library), `oxi_export` (library -> card) | MKII manual p.23, p.277; decoding needs `RACK_LIBRARY` |

Things to ask for: *"show 'HELLO' on the OXI screen"*, *"light the grid as a VU meter"*, *"run clock at 128 and
start"*, *"jump to pattern column 3"*, *"tell me which pads I press"*.

### Optional: project files

The project tools read and edit `.oxipro` projects kept in a rack-library-style
checkout (`projects/<slug>/` with the `.oxipro`, `project.md` and `patterns.json`; edits go through its
`tools/serve.py` save path and are committed with git). Set `RACK_LIBRARY` to the checkout to enable them:

```
claude mcp add --scope user oxi-one -e RACK_LIBRARY=/path/to/rack-library -- python -m oxi_mcp.server
```

Without it those tools return an error and everything else works.

### The SD card wins

Projects are played and saved on the OXI, so the microSD card is the source of truth. With the card in a
reader, the tools find it by the folders the OXI creates at power-up (`Projects`, `Settings`, `Instruments`,
`Grooves`, `Scales`); pass `card_path` or set `OXI_CARD` if it is somewhere else.

- `oxi_card` lists the card's projects and says whether each is the same as the library's copy, differs, or is new.
- `oxi_card_pull` takes the card's version into the library (notes in `project.md` are kept; git keeps the old file).
- `oxi_edit_pattern` on a project that is on the card pulls the card's version first, edits it, and writes it back
  to the card, refusing if the card changed in between. `on_card=False` edits only the library.
- `oxi_export` copies a library project onto the card, and never overwrites a card file that differs.

The OXI keeps projects in 20 internal slots; a project reaches the card only through
Project menu (Shift + Perf) > Copy to SD Card, and comes back through Load from SD Card (manual p.23).

## Protocol notes

- SysEx header MKII `F0 00 21 5B 01 01` (MK1 `.. 00 01`). Values after the category byte are nibbled (high, low).
- Remote mode: enter `06 55` -> ACK `06 53`; exit `00`; pad `01 id r g b`; button LED `02 id value`
  (off 0, on 1, blink 2, blink fast 4, short 6); OLED `03` + 1024 bytes (128x64, 1 bit per pixel).
- Pad id = x + 16*y on 16 columns x 8 rows. The sheet's text says x 0-7 / y 0-15, but its "row 2 col 4" example
  (id 0x13) only fits 16x8; the tests encode both of its examples byte for byte.
- Events in remote mode: pads = notes on ch 1 (x + 16y), function buttons = notes on ch 2 (FunctionButtons_e order),
  encoders = CC on ch 1, value 1-8 clockwise / 0x11-0x18 counter-clockwise.

### Not yet verified on hardware

- The remote-mode ACK and event decoding.
- OLED pixel packing: undocumented. `oxi_oled(layout="pages")` is SSD1306-style; try `"rows"` if text is scrambled.
- SYSTEM version (`03 00`) and PROJECT (`05 ..`) requests come from the MK1 desktop app and may not exist on MKII.

Reports from MKII owners are welcome: open an issue with your firmware version and what you saw.

## Prior art

- OXI Remote Control Reference (official): https://docs.google.com/spreadsheets/d/1Yccnrluv10QL_PauMmtCt64EtYjfSeZEXKrlrw8P24w
- [faqteur/OXI-Desktop](https://github.com/faqteur/OXI-Desktop): MK1 desktop app; SysEx categories and project transfer.
- [wylie39/oxi-one-web](https://github.com/wylie39/oxi-one-web): TypeScript port of the MK1 transfer.
- [OXI-Instruments/oxi_midi_import](https://github.com/OXI-Instruments/oxi_midi_import): MIDI file to pattern models (no SysEx).
- [thorinside/bitwig_oxi_one_kotlin](https://github.com/thorinside/bitwig_oxi_one_kotlin): Bitwig remote extension.
- [Ziforge/cirklon-mcp](https://github.com/Ziforge/cirklon-mcp): MCP server for the Sequentix Cirklon; this one follows its shape.

## License

MIT
