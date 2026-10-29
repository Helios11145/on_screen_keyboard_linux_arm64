#!/usr/bin/env python3
"""Simple on-screen keyboard. Types via /dev/uinput (works on X11 and Wayland)."""
import os, fcntl, struct, time, sys
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, Gio, GLib
GLib.set_prgname("simple-osk")
Gdk.set_program_class("simple-osk")
HAVE_LS = False
try:
    gi.require_version("GtkLayerShell", "0.1")
    from gi.repository import GtkLayerShell
    HAVE_LS = True
except Exception:
    pass

# ---- uinput ----------------------------------------------------------------
UI_SET_EVBIT, UI_SET_KEYBIT = 0x40045564, 0x40045565
UI_DEV_CREATE, UI_DEV_DESTROY = 0x5501, 0x5502
EV_SYN, EV_KEY = 0, 1

class Uinput:
    def __init__(self):
        self.fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_KEY)
        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_SYN)
        for k in range(1, 256):
            fcntl.ioctl(self.fd, UI_SET_KEYBIT, k)
        dev = struct.pack("80sHHHHi", b"Simple OSK", 0x03, 0x1, 0x1, 1, 0)
        dev += struct.pack("256i", *([0] * 256))
        os.write(self.fd, dev)
        fcntl.ioctl(self.fd, UI_DEV_CREATE)
        time.sleep(1.0)  # let the system notice the new device

    def _ev(self, t, c, v):
        os.write(self.fd, struct.pack("llHHi", 0, 0, t, c, v))

    def key(self, code, down):
        self._ev(EV_KEY, code, 1 if down else 0)
        self._ev(EV_SYN, 0, 0)

    def close(self):
        try: fcntl.ioctl(self.fd, UI_DEV_DESTROY)
        except Exception: pass
        os.close(self.fd)


# ---- xdg-desktop-portal RemoteDesktop (Wayland/KDE, permission by tapping) ------
class Portal:
    NAME, PATH = "org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop"
    IFACE = "org.freedesktop.portal.RemoteDesktop"
    TOKEN_FILE = os.path.expanduser("~/.config/simple-osk-token")

    def __init__(self):
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.sender = self.bus.get_unique_name()[1:].replace(".", "_")
        self.n = 0
        V = GLib.Variant
        res = self._request("CreateSession", lambda t: V("(a{sv})", ({
            "handle_token": V("s", t), "session_handle_token": V("s", "osk%d" % os.getpid())},)))
        self.session = res["session_handle"]
        try: tok = open(self.TOKEN_FILE).read().strip()
        except Exception: tok = ""
        def sel(t):
            o = {"handle_token": V("s", t), "types": V("u", 1), "persist_mode": V("u", 2)}
            if tok: o["restore_token"] = V("s", tok)
            return V("(oa{sv})", (self.session, o))
        self._request("SelectDevices", sel)
        res = self._request("Start", lambda t: V("(osa{sv})", (self.session, "", {"handle_token": V("s", t)})))
        if res.get("restore_token"):
            try:
                os.makedirs(os.path.dirname(self.TOKEN_FILE), exist_ok=True)
                open(self.TOKEN_FILE, "w").write(res["restore_token"])
            except Exception: pass

    def _request(self, method, build):
        self.n += 1
        tok = "osk%d_%d" % (os.getpid(), self.n)
        rpath = "/org/freedesktop/portal/desktop/request/%s/%s" % (self.sender, tok)
        loop, out = GLib.MainLoop(), {}
        def on_resp(*a):
            out["code"], out["res"] = a[5].unpack(); loop.quit()
        sid = self.bus.signal_subscribe(self.NAME, "org.freedesktop.portal.Request", "Response",
                                        rpath, None, Gio.DBusSignalFlags.NONE, on_resp)
        try:
            self.bus.call_sync(self.NAME, self.PATH, self.IFACE, method, build(tok),
                               None, Gio.DBusCallFlags.NONE, -1, None)
            GLib.timeout_add_seconds(120, loop.quit)
            loop.run()
        finally:
            self.bus.signal_unsubscribe(sid)
        if out.get("code") != 0:
            raise RuntimeError("Portal %s was denied or timed out" % method)
        return out["res"]

    def key(self, code, down):
        self.bus.call_sync(self.NAME, self.PATH, self.IFACE, "NotifyKeyboardKeycode",
            GLib.Variant("(oa{sv}iu)", (self.session, {}, code, 1 if down else 0)),
            None, Gio.DBusCallFlags.NONE, -1, None)

    def close(self): pass


# ---- KDE: window rule so KWin never gives this window focus -----------------------
def ensure_kwin_rule():
    try:
        import configparser, uuid
        path = os.path.expanduser("~/.config/kwinrulesrc")
        cp = configparser.RawConfigParser(); cp.optionxform = str
        cp.read(path)
        if not cp.has_section("General"): cp.add_section("General")
        ids = [x for x in cp.get("General", "rules", fallback="").split(",") if x]
        for i in ids:
            if cp.has_section(i) and cp.get(i, "Description", fallback="") == "Simple OSK":
                return
        rid = str(uuid.uuid4()); ids.append(rid)
        cp.set("General", "rules", ",".join(ids)); cp.set("General", "count", str(len(ids)))
        cp.add_section(rid)
        for k, v in dict(Description="Simple OSK", wmclass="simple-osk", wmclassmatch="1",
                         acceptfocus="false", acceptfocusrule="2", above="true", aboverule="2").items():
            cp.set(rid, k, v)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f: cp.write(f, space_around_delimiters=False)
        Gio.bus_get_sync(Gio.BusType.SESSION, None).call_sync(
            "org.kde.KWin", "/KWin", "org.kde.KWin", "reconfigure", None, None,
            Gio.DBusCallFlags.NONE, 2000, None)
        time.sleep(0.5)
    except Exception:
        pass

# ---- layout: (label, shifted label, linux keycode, width units) --------------
def K(l, s, c, w=2): return (l, s, c, w)
ROWS = [
    [K("`","~",41), *[K(str(i%10), s, 1+i) for i, s in zip(range(1,11), "!@#$%^&*()")],
     K("-","_",12), K("=","+",13), K("⌫","⌫",14,4)],
    [K("Tab","Tab",15,3), *[K(c, c.upper(), k) for c, k in zip("qwertyuiop",
     [16,17,18,19,20,21,22,23,24,25])], K("[","{",26), K("]","}",27), K("\\","|",43,3)],
    [K("Caps","Caps",58,4), *[K(c, c.upper(), k) for c, k in zip("asdfghjkl",
     [30,31,32,33,34,35,36,37,38])], K(";",":",39), K("'","\"",40), K("Enter","Enter",28,4)],
    [K("Shift","Shift",42,5), *[K(c, c.upper(), k) for c, k in zip("zxcvbnm",
     [44,45,46,47,48,49,50])], K(",","<",51), K(".",">",52), K("/","?",53), K("Shift","Shift",54,5)],
    [K("Esc","Esc",1,2), K("Ctrl","Ctrl",29,3), K("Alt","Alt",56,3), K(" ","Space",57,14),
     K("←","←",105), K("↓","↓",108), K("↑","↑",103), K("→","→",106)],
]
MODS = {42: "shift", 54: "shift", 29: "ctrl", 56: "alt"}
MOD_CODE = {"shift": 42, "ctrl": 29, "alt": 56}

CSS = b"""
button { font-size: 20px; padding: 0; min-height: 0; min-width: 0; }
button.active { background: #3584e4; color: white; }
"""

class OSK(Gtk.Window):
    def __init__(self, ui, name):
        super().__init__(title="Keyboard")
        self.ui, self.name = ui, name
        self.mods = {"shift": False, "ctrl": False, "alt": False}
        self.labels = []   # (button, label, shifted)
        self.modbtns = []  # (button, modname)
        if HAVE_LS and GtkLayerShell.is_supported():   # Wayland: dock at bottom, never takes focus
            GtkLayerShell.init_for_window(self)
            GtkLayerShell.set_layer(self, GtkLayerShell.Layer.TOP)
            for e in (GtkLayerShell.Edge.BOTTOM, GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT):
                GtkLayerShell.set_anchor(self, e, True)
            try: GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.NONE)
            except AttributeError: GtkLayerShell.set_keyboard_interactivity(self, False)
        else:
            self.set_accept_focus(False)       # X11: don't steal focus from the target app
            self.set_keep_above(True)
            self.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        self.set_default_size(1000, 300)
        p = Gtk.CssProvider(); p.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), p,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        grid = Gtk.Grid(column_homogeneous=True, row_homogeneous=True,
                        row_spacing=3, column_spacing=3, margin=4)
        for r, row in enumerate(ROWS):
            x = 0
            for label, shifted, code, w in row:
                b = Gtk.Button(label=label)
                b.set_can_focus(False); b.set_focus_on_click(False)
                b.set_hexpand(True); b.set_vexpand(True)
                b.connect("clicked", self.on_key, code)
                grid.attach(b, x, r, w, 1); x += w
                self.labels.append((b, label, shifted, code))
                if code in MODS: self.modbtns.append((b, MODS[code]))
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        vbox.pack_start(grid, True, True, 0)
        bar = Gtk.Box(spacing=6)
        self.status = Gtk.Label(label="Method: %s. Tap a key." % name, xalign=0, margin_start=6)
        self.swbtn = Gtk.Button(label="Switch method")
        self.swbtn.set_can_focus(False); self.swbtn.set_focus_on_click(False)
        self.swbtn.connect("clicked", self.on_switch)
        bar.pack_start(self.status, True, True, 0); bar.pack_end(self.swbtn, False, False, 4)
        vbox.pack_start(bar, False, False, 2)
        self.add(vbox)
        self.connect("destroy", Gtk.main_quit)

    def on_key(self, _b, code):
        if code in MODS:
            m = MODS[code]; self.mods[m] = not self.mods[m]; self.refresh(); return
        try:
            held = [MOD_CODE[m] for m, on in self.mods.items() if on]
            for c in held: self.ui.key(c, True)
            self.ui.key(code, True); time.sleep(0.02); self.ui.key(code, False)
            for c in reversed(held): self.ui.key(c, False)
            self.status.set_text("Method: %s. Sent key %d." % (self.name, code))
        except Exception as e:
            self.status.set_text("Method: %s. ERROR: %s" % (self.name, e))
        if any(self.mods.values()):
            self.mods = {k: False for k in self.mods}; self.refresh()

    def on_switch(self, _b):
        other = "portal" if self.name == "uinput" else "uinput"
        self.status.set_text("Switching to %s... (if KDE asks, tap Allow)" % other)
        while Gtk.events_pending(): Gtk.main_iteration()
        try:
            new = Portal() if other == "portal" else Uinput()
        except Exception as e:
            self.status.set_text("Could not use %s: %s" % (other, e)); return
        old = self.ui; self.ui, self.name = new, other
        try: old.close()
        except Exception: pass
        self.status.set_text("Method: %s. Tap a key." % other)

    def refresh(self):
        s = self.mods["shift"]
        for b, label, shifted, code in self.labels:
            if code not in MODS and len(label) == 1 or label in "`-=[]\\;',./":
                b.set_label(shifted if s else label)
        for b, m in self.modbtns:
            ctx = b.get_style_context()
            (ctx.add_class if self.mods[m] else ctx.remove_class)("active")

def main():
    ui = None; errs = []; name = ""
    for B in (Uinput, Portal):
        try: ui = B(); name = B.__name__.lower(); break
        except Exception as e: errs.append("%s: %s" % (B.__name__, e))
    if ui is None:
        d = Gtk.MessageDialog(text="Can't send key presses", message_type=Gtk.MessageType.ERROR,
                              buttons=Gtk.ButtonsType.CLOSE)
        d.format_secondary_text("\n".join(errs) + "\n\nWhen KDE asks to allow remote control / keyboard input, "
                                "tap Share or Allow, then start this app again.")
        d.run(); sys.exit(1)
    ensure_kwin_rule()
    w = OSK(ui, name); w.show_all(); Gtk.main(); ui.close()

if __name__ == "__main__":
    main()
