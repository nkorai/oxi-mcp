"""USB-MIDI connection to the OXI ONE: port discovery, send, and a background event log."""
import collections
import threading
import time

import rtmidi

from . import sysex as S


def _match(names, hint):
    hints = [hint.lower()] if hint else ["oxi"]
    for i, n in enumerate(names):
        if any(h in n.lower() for h in hints):
            return i, n
    return None, None


def ports():
    try:
        return {"inputs": rtmidi.MidiIn().get_ports(), "outputs": rtmidi.MidiOut().get_ports()}
    except rtmidi.RtMidiError as e:  # no MIDI backend, e.g. Linux without /dev/snd/seq
        return {"inputs": [], "outputs": [], "error": str(e)}


class Device:
    def __init__(self):
        self.out = self.inp = None
        self.out_name = self.in_name = None
        self.events = collections.deque(maxlen=2000)
        self.cond = threading.Condition()
        self.n = 0
        self.remote = False
        self.lock = threading.Lock()
        self._clock = None
        self._sysex = None  # a long SysEx still arriving in pieces

    # --- connection --------------------------------------------------------------------------
    @property
    def connected(self):
        return self.out is not None

    def connect(self, hint=None):
        self.close()
        out, inp = rtmidi.MidiOut(), rtmidi.MidiIn()
        oi, on = _match(out.get_ports(), hint)
        if oi is None:
            raise RuntimeError(f"no MIDI output matching {hint or 'OXI'!r}; outputs: {out.get_ports()} "
                               "(plug the OXI into USB; on Windows only one app can hold the port)")
        out.open_port(oi)
        self.out, self.out_name = out, on
        ii, inn = _match(inp.get_ports(), hint)
        if ii is not None:
            inp.ignore_types(sysex=False, timing=True, active_sense=True)
            inp.set_callback(self._on_msg)
            inp.open_port(ii)
            self.inp, self.in_name = inp, inn
        return {"output": on, "input": inn}

    def close(self):
        self.clock_stop()
        for p in (self.inp, self.out):
            if p is not None:
                try:
                    p.close_port()
                except Exception:
                    pass
        self.out = self.inp = self.out_name = self.in_name = None
        self.remote = False

    def ensure(self):
        if not self.connected:
            self.connect()

    # --- io ----------------------------------------------------------------------------------
    def _on_msg(self, event, _data=None):
        msg, _dt = event
        # Windows MIDI hands a long SysEx over in 1024-byte pieces: join them up to the F7
        if msg and msg[0] == 0xF0 and msg[-1] != 0xF7:
            self._sysex = list(msg)
            return
        if self._sysex is not None and msg and (msg[0] < 0x80 or msg[0] == 0xF7):
            self._sysex += msg
            if self._sysex[-1] != 0xF7:
                return
            msg, self._sysex = self._sysex, None
        ev = S.decode_event(msg)
        if ev is None:
            return
        ev["t"] = round(time.time(), 3)
        with self.cond:
            self.n += 1
            ev["n"] = self.n
            self.events.append(ev)
            self.cond.notify_all()

    def send(self, msg):
        self.ensure()
        with self.lock:
            self.out.send_message(list(msg))

    def send_many(self, msgs, gap=0.0):
        self.ensure()
        with self.lock:
            for m in msgs:
                self.out.send_message(list(m))
                if gap:
                    time.sleep(gap)

    def take_events(self, clear=True, types=None):
        with self.cond:
            evs = [e for e in self.events if not types or e["type"] in types]
            if clear:
                self.events.clear()
        return evs

    def request(self, msg, pred, timeout=1.0):
        """Send msg and wait for a matching reply. Needs the input port."""
        if self.inp is None:
            raise RuntimeError("no OXI MIDI input port open, so replies cannot be read")
        with self.cond:
            start = self.n
        self.send(msg)
        end = time.time() + timeout
        with self.cond:
            while True:
                for ev in self.events:
                    if ev["n"] > start and pred(ev):
                        return ev
                left = end - time.time()
                if left <= 0:
                    return None
                self.cond.wait(left)

    # --- MIDI clock --------------------------------------------------------------------------
    def clock_start(self, bpm, send_start=True):
        self.clock_stop()
        self.ensure()
        stop = threading.Event()
        period = 60.0 / (bpm * 24)

        def run():
            if send_start:
                self.send([0xFA])
            nxt = time.perf_counter()
            while not stop.is_set():
                self.send([0xF8])
                nxt += period
                d = nxt - time.perf_counter()
                if d > 0:
                    stop.wait(d)

        t = threading.Thread(target=run, daemon=True, name="oxi-clock")
        self._clock = (stop, t, bpm)
        t.start()

    def clock_stop(self, send_stop=False):
        if self._clock:
            stop, t, _ = self._clock
            stop.set()
            t.join(1)
            self._clock = None
            if send_stop and self.connected:
                self.send([0xFC])

    @property
    def clock_bpm(self):
        return self._clock[2] if self._clock else None
