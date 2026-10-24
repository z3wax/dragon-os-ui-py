# DragonOS Desktop UI (GTK3) — Alpine Edition
# Modern glass desktop: top status bar, magnifying dock, launchpad, spotlight,
# real battery/network from sysfs, Alpine-friendly power actions, smooth parallax.
import gi
import os
import json
import sys
import shutil
import difflib
import uuid
import subprocess
from datetime import datetime

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib, Pango, GObject

from helpers import make_target_entries, info_dialog, load_icon, ensure_desktop_folder
from apps import Notepad, Calculator, Gallery, FileManager, MusicPlayer, SettingsDialog

APP_TITLE = "DragonOS"
DESKTOP_FOLDER = os.path.expanduser("~/Desktop")
STATE_FILE = os.path.join(DESKTOP_FOLDER, ".desktop_state.json")
DEFAULT_WALLPAPER = os.path.abspath("wallpaper.png") if os.path.exists("wallpaper.png") else None

APPS = {
    "Terminal": "utilities-terminal",
    "FileManager": "folder",
    "Notepad": "accessories-text-editor",
    "Gallery": "multimedia-photo-viewer",
    "MusicPlayer": "media-playback-start",
    "Calculator": "accessories-calculator",
    "Settings": "emblem-system",
}

ICON_SIZE = 52
SNAP_TO_GRID = True
GRID_SIZE = 56

ACCENT = "#7c8cff"
ACCENT2 = "#a855f7"
BgTint = "#0b0e13"

TEXT_EXTS = (".txt", ".py", ".md", ".json", ".log", ".cfg", ".ini")
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".webm", ".avi")


# ---------- Small utilities ----------

def image_average_luminance(path):
    try:
        pb = GdkPixbuf.Pixbuf.new_from_file(path)
        small = pb.scale_simple(8, 8, GdkPixbuf.InterpType.BILINEAR)
        pixels = small.get_pixels()
        ch = small.get_n_channels()
        stride = small.get_rowstride()
        total = 0.0
        cnt = 0
        for yy in range(small.get_height()):
            row_off = yy * stride
            for xx in range(small.get_width()):
                off = row_off + xx * ch
                if off + 2 < len(pixels):
                    r = pixels[off]
                    g = pixels[off + 1]
                    b = pixels[off + 2]
                    total += (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0
                    cnt += 1
        return (total / cnt) if cnt else 0.5
    except Exception:
        return 0.5


def fuzzy_matches(query, choices, cutoff=0.45):
    q = (query or "").lower()
    if not q:
        return []
    scores = [(difflib.SequenceMatcher(None, q, c.lower()).ratio(), c) for c in choices]
    return [c for s, c in sorted(scores, reverse=True) if s >= cutoff]


def fade_in(win, duration=180):
    steps = 12
    step_ms = max(1, int(duration / steps))
    idx = [0]

    def step():
        idx[0] += 1
        try:
            win.set_opacity(min(1.0, idx[0] / steps))
            win.show_all()
        except Exception:
            return False
        return idx[0] < steps

    try:
        win.set_opacity(0.0)
        win.show_all()
        GLib.timeout_add(step_ms, step)
    except Exception:
        try:
            win.show_all()
        except Exception:
            pass


def fade_out(win, duration=160):
    steps = 10
    step_ms = max(1, int(duration / steps))
    idx = [0]

    def step():
        idx[0] += 1
        try:
            win.set_opacity(max(0.0, 1.0 - idx[0] / steps))
        except Exception:
            return False
        if idx[0] >= steps:
            try:
                win.hide()
                win.set_opacity(1.0)
            except Exception:
                pass
            return False
        return True

    try:
        GLib.timeout_add(step_ms, step)
    except Exception:
        try:
            win.hide()
        except Exception:
            pass


def enable_rounded_window(win):
    """Allow CSS rounded corners/translucency when a compositor provides RGBA."""
    try:
        screen = win.get_screen()
        visual = screen.get_rgba_visual()
        if visual:
            win.set_visual(visual)
        win.set_app_paintable(True)
        win.connect("draw", _rounded_draw)
    except Exception:
        pass


def _rounded_draw(widget, cr):
    try:
        ctx = widget.get_style_context()
        alloc = widget.get_allocation()
        Gtk.render_background(ctx, cr, 0, 0, alloc.width, alloc.height)
    except Exception:
        pass
    return False


def _read_sys(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except Exception:
        return ""


def battery_state():
    """(percent or None, charging: bool) from /sys/class/power_supply (no deps)."""
    try:
        for name in os.listdir("/sys/class/power_supply"):
            ul = name.upper()
            if not (ul.startswith("BAT") or "battery" in name.lower() or ul == "C1"):
                continue
            base = os.path.join("/sys/class/power_supply", name)
            cap = _read_sys(os.path.join(base, "capacity"))
            try:
                pct = int(float(cap))
            except Exception:
                full = _read_sys(os.path.join(base, "charge_full")) or _read_sys(os.path.join(base, "energy_full"))
                now = _read_sys(os.path.join(base, "charge_now")) or _read_sys(os.path.join(base, "energy_now"))
                try:
                    pct = int(round(float(now) / float(full) * 100)) if full else None
                except Exception:
                    pct = None
            if pct is None:
                continue
            status = _read_sys(os.path.join(base, "status")).lower()
            return max(0, min(100, pct)), status in ("charging", "full")
    except Exception:
        pass
    return None, False


def network_state():
    """'wireless' | 'wired' | None from /sys/class/net (no deps)."""
    try:
        for name in sorted(os.listdir("/sys/class/net")):
            if name == "lo" or name.startswith(("docker", "veth", "virbr", "vmnet")):
                continue
            base = os.path.join("/sys/class/net", name)
            operstate = _read_sys(os.path.join(base, "operstate")).lower()
            carrier = _read_sys(os.path.join(base, "carrier"))
            if operstate in ("up", "unknown") and carrier in ("1", ""):
                return "wireless" if name.startswith(("wl", "ra", "az")) else "wired"
    except Exception:
        pass
    return None


def battery_icon_name(pct, charging):
    if pct is None:
        return None
    name = "battery-full-charging-symbolic" if charging else "battery-full-symbolic"
    if pct <= 85:
        name = ("battery-good" if pct > 40 else "battery-caution") + ("-charging" if charging else "") + "-symbolic"
    theme = Gtk.IconTheme.get_default()
    if not theme.has_icon(name):
        name = "battery-full-symbolic"
    return name


def pixbuf_from_ref(ref, size=ICON_SIZE):
    if not ref:
        return None
    try:
        if os.path.isfile(ref):
            return GdkPixbuf.Pixbuf.new_from_file_at_size(ref, size, size)
        for name in (ref, "application-x-executable", "text-x-generic"):
            try:
                return Gtk.IconTheme.get_default().load_icon(name, size, Gtk.IconLookupFlags.FORCE_SIZE)
            except Exception:
                continue
    except Exception:
        pass
    return _adwaita_pixbuf(ref, size)


def _fallback_themes():
    """Server icon theme obyektlari ro'yhati (birinchi — Adwaita, keyin moslashuvchanlar)."""
    try:
        if _fallback_themes.list is None:
            lst = []
            for nm in ("Adwaita", "elementary-xfce", "breeze", "default"):
                try:
                    t = Gtk.IconTheme.new()
                    t.set_custom_theme(nm)
                    lst.append(t)
                except Exception:
                    pass
            _fallback_themes.list = lst
        return _fallback_themes.list
    except Exception:
        return []

_fallback_themes.list = None


def theme_icon(name, size=16):
    """Gtk.Image with robust icon-theme fallback (system -> Adwaita)."""
    pb = pixbuf_from_ref(name, size) or _adwaita_pixbuf(name, size)
    img = Gtk.Image()
    if pb:
        img.set_from_pixbuf(pb)
    else:
        img.set_from_icon_name(name, Gtk.IconSize.MENU)
    return img


def _adwaita_pixbuf(name, size=ICON_SIZE):
    """Barqarorni ikonkalarni ol: avval system, uzra Adwaita va taniqlar."""
    try:
        themes = _fallback_themes()
        for n in (name, "application-x-executable", "text-x-generic"):
            for t in themes:
                try:
                    return t.load_icon(n, size, Gtk.IconLookupFlags.FORCE_SIZE)
                except Exception:
                    continue
    except Exception:
        pass
    return None


def _power_system(action="off"):
    """Alpine-friendly poweroff/reboot with graceful escalation fallbacks."""
    prog = "poweroff" if action in ("off", "poweroff") else "reboot"
    loginctl = "poweroff" if action in ("off", "poweroff") else "reboot"
    candidates = [
        f"doas -n {prog}",
        f"sudo -n {prog}",
        f"pkexec {prog}",
        f"loginctl {loginctl}",
        prog,
    ]
    for cmd in candidates:
        try:
            if subprocess.call(["sh", "-c", cmd],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0:
                return True
        except Exception:
            continue
    return False


# ---------- Wallpaper ----------

class Wallpaper(Gtk.EventBox):
    def __init__(self, path=None, parallax=True, interactive=False):
        super().__init__()
        self.set_name("wallpaper")

        try:
            self.set_visible_window(False)
        except Exception:
            pass

        if not interactive:
            try:
                self.set_sensitive(False)
                self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
                self.connect("button-press-event", lambda *_: True)
            except Exception:
                pass

        self.image = Gtk.Image()
        self.add(self.image)

        self._pb = None
        self._path = None
        self._parallax_enabled = bool(parallax)
        self._parallax_offset = (0.0, 0.0)
        self._scaled = None
        self._scaled_alloc = None

        if path:
            self.set_wallpaper(path)

    def set_parallax(self, enabled=True):
        self._parallax_enabled = bool(enabled)
        if not self._parallax_enabled:
            self._update(self.get_allocation())

    def set_parallax_offset(self, nx=0.0, ny=0.0):
        try:
            nx = max(-1.0, min(1.0, float(nx)))
            ny = max(-1.0, min(1.0, float(ny)))
            self._parallax_offset = (nx, ny)
            self._update(self.get_allocation())
        except Exception:
            pass

    def get_path(self):
        return self._path

    def set_wallpaper(self, path=None):
        try:
            if not path or not os.path.exists(path):
                self._path = None
                self._pb = None
                self._scaled = None
                try:
                    self.image.clear()
                except Exception:
                    pass
                return
            self._path = path
            self._pb = GdkPixbuf.Pixbuf.new_from_file(path)
            self._scaled = None
            self._scaled_alloc = None
            self._update(self.get_allocation())
        except Exception:
            self._pb = None
            self._scaled = None

    def do_size_allocate(self, allocation):
        try:
            Gtk.EventBox.do_size_allocate(self, allocation)
        except Exception:
            pass
        try:
            if self._pb:
                GLib.idle_add(self._resize_scaled, allocation)
        except Exception:
            pass

    def _resize_scaled(self, allocation=None):
        """Scale pixbuf once per allocation (with parallax margin), then crops are cheap."""
        alloc = allocation or self.get_allocation()
        w, h = alloc.width, alloc.height
        if not self._pb or w <= 0 or h <= 0:
            return False
        mx = max(2, int(w * 0.05))
        my = max(2, int(h * 0.05))
        key = (w + 2 * mx, h + 2 * my)
        if self._scaled_alloc == key and self._scaled:
            return False
        pbw, pbh = self._pb.get_width(), self._pb.get_height()
        scale = max(key[0] / max(1, pbw), key[1] / max(1, pbh))
        nw = max(1, int(round(pbw * scale)))
        nh = max(1, int(round(pbh * scale)))
        try:
            self._scaled = self._pb.scale_simple(nw, nh, GdkPixbuf.InterpType.BILINEAR)
            self._scaled_alloc = key
        except Exception:
            self._scaled = self._pb
        self._update(alloc)
        return False

    def _update(self, allocation=None):
        alloc = allocation or self.get_allocation()
        w, h = alloc.width, alloc.height
        if w <= 0 or h <= 0 or not self._pb or not self._scaled:
            return False

        mx = max(2, int(w * 0.05))
        my = max(2, int(h * 0.05))
        scaled = self._scaled
        sw, sh = scaled.get_width(), scaled.get_height()

        ox0 = max(0, (sw - w) // 2)
        oy0 = max(0, (sh - h) // 2)

        if self._parallax_enabled:
            px, py = self._parallax_offset
            ox = max(0, min(sw - w, ox0 + int(px * mx)))
            oy = max(0, min(sh - h, oy0 + int(py * my)))
        else:
            ox, oy = ox0, oy0

        try:
            cropped = scaled.new_subpixbuf(ox, oy, min(w, sw - ox), min(h, sh - oy))
            self.image.set_from_pixbuf(cropped)
        except Exception:
            try:
                self.image.set_from_pixbuf(scaled)
            except Exception:
                self.image.clear()
        return False


# ---------- Launchpad ----------

class Launchpad(Gtk.Window):
    def __init__(self, parent):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.parent = parent
        enable_rounded_window(self)
        self.set_name("launchpad")
        self.set_transient_for(parent)
        self.set_modal(False)
        self.set_decorated(False)
        self.set_default_size(680, 520)
        self.set_size_request(540, 420)
        self.set_position(Gtk.WindowPosition.CENTER_ALWAYS)
        self.set_type_hint(Gdk.WindowTypeHint.DIALOG)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        box.set_border_width(24)

        self.search = Gtk.Entry()
        self.search.set_name("lp-search")
        self.search.set_halign(Gtk.Align.CENTER)
        self.search.set_width_chars(30)
        self.search.set_placeholder_text("Search apps…")
        try:
            self.search.set_icon_from_icon_name(Gtk.EntryIconPosition.PRIMARY, "edit-find-symbolic")
            self.search.set_icon_sensitive(Gtk.EntryIconPosition.PRIMARY, False)
        except Exception:
            pass
        self.search.connect("changed", lambda e: self.populate(e.get_text()))
        self.search.connect("activate", self._on_search_activate)
        box.pack_start(self.search, False, False, 0)

        sc = Gtk.ScrolledWindow()
        sc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sc.set_name("lp-sc")
        grid = Gtk.FlowBox()
        grid.set_name("lp-grid")
        grid.set_max_children_per_line(6)
        grid.set_min_children_per_line(6)
        grid.set_selection_mode(Gtk.SelectionMode.SINGLE)
        grid.set_valign(Gtk.Align.START)
        self._grid = grid
        grid.connect("child-activated", self._on_child_activated)
        sc.add(grid)
        box.pack_start(sc, True, True, 0)

        self.add(box)

        self.connect("key-press-event", self._on_key)
        self.connect("focus-in-event", self._on_focus_in)
        self.connect("focus-out-event", self._on_focus_out)
        self.show_all()
        self.populate()
        self.hide()

    def _title_for(self, t):
        return {"FileManager": "Files"}.get(t, t)

    def populate(self, filter_text=""):
        try:
            children = self._grid.get_children()
            for c in children:
                self._grid.remove(c)
        except Exception:
            pass
        ft = (filter_text or "").strip()
        titles = list(APPS.keys())
        if ft:
            matches = fuzzy_matches(ft, titles, cutoff=0.35)
            if not matches:
                matches = [t for t in titles if ft.lower() in t.lower() or ft.lower() in self._title_for(t).lower()]
        else:
            matches = titles
        for title in matches:
            icon = APPS.get(title, "application-x-executable")
            btn = Gtk.Button()
            btn.set_relief(Gtk.ReliefStyle.NONE)
            btn.get_style_context().add_class("lp-tile")

            pb = pixbuf_from_ref(icon, 62)
            img = Gtk.Image.new_from_pixbuf(pb) if pb else load_icon(icon, 48)
            v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            v.set_halign(Gtk.Align.CENTER)
            v.pack_start(img, False, False, 0)
            lbl = Gtk.Label(label=self._title_for(title))
            lbl.get_style_context().add_class("lp-label")
            lbl.set_xalign(0.5)
            lbl.set_ellipsize(Pango.EllipsizeMode.END)
            v.pack_start(lbl, False, False, 0)
            btn.add(v)
            btn.set_tooltip_text(title)
            btn.connect("clicked", lambda _w, t=title: self._on_launch(t))
            self._grid.add(btn)
            try:
                btn.get_parent()._title = title
            except Exception:
                pass
        self.show_all()

    def _on_child_activated(self, _grid, child):
        try:
            title = getattr(child, "_title", None)
            if title:
                self._on_launch(title)
        except Exception:
            pass

    def _on_search_activate(self, entry):
        if not entry.get_text().strip():
            return
        try:
            for child in self._grid.get_children():
                try:
                    btn = child.get_child() if isinstance(child, Gtk.FlowBoxChild) else child
                    btn.activate()
                    break
                except Exception:
                    continue
        except Exception:
            pass

    def _on_launch(self, t):
        try:
            self.hide()
            self.parent.open_app(t)
        except Exception:
            info_dialog(self, "Xato", f"{t} ochib bo'lmadi")

    def _on_key(self, win, ev):
        try:
            name = (Gdk.keyval_name(ev.keyval) or "").lower()
            if name == "escape":
                self.hide_lp()
                return True
            if name in ("down", "kp_down", "up", "kp_up"):
                children = self._grid.get_children()
                if not children:
                    return True
                fwd = name.startswith(("down", "kp_down"))
                sel = self._grid.get_selected_children()
                try:
                    cur = children.index(sel[0]) if sel else -1
                except Exception:
                    cur = -1
                while True:
                    cur = cur + (1 if fwd else -1)
                    if not (0 <= cur < len(children)):
                        if cur < 0:
                            self.search.grab_focus()
                        return True
                    child = children[cur]
                    try:
                        self._grid.select_child(child)
                        child.grab_focus()
                        return True
                    except Exception:
                        continue
        except Exception:
            pass
        return False

    def _on_focus_in(self, *_):
        try:
            self.parent.modal_backdrop.show_all()
        except Exception:
            pass
        return False

    def _on_focus_out(self, *_):
        if not self.get_visible():
            return False
        try:
            self.hide_lp()
        except Exception:
            pass
        return False

    def show_lp(self):
        try:
            self.search.set_text("")
        except Exception:
            pass
        self.populate("")
        fade_in(self)
        self.present()
        GLib.idle_add(lambda: (self.search.grab_focus(), False)[1])

    def hide_lp(self):
        fade_out(self)


# ---------- Spotlight (Super+Space) ----------

class Spotlight(Gtk.Window):
    def __init__(self, parent):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.parent = parent
        enable_rounded_window(self)
        self.set_name("spotlight")
        self.set_transient_for(parent)
        self.set_decorated(False)
        self.set_modal(False)
        self.set_default_size(620, 96)
        self.set_size_request(520, 96)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_resizable(True)
        self.set_type_hint(Gdk.WindowTypeHint.DIALOG)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.set_border_width(10)

        self.entry = Gtk.Entry()
        self.entry.set_name("spotlight-entry")
        self.entry.set_placeholder_text("Search apps and desktop files…")
        self.entry.set_width_chars(44)
        try:
            self.entry.set_icon_from_icon_name(Gtk.EntryIconPosition.PRIMARY, "edit-find-symbolic")
            self.entry.set_icon_sensitive(Gtk.EntryIconPosition.PRIMARY, False)
        except Exception:
            pass
        self.entry.connect("changed", lambda e: self._on_change(e.get_text()))
        self.entry.connect("activate", lambda e: self._activate_selected())
        box.pack_start(self.entry, False, False, 4)

        self.listbox = Gtk.ListBox()
        self.listbox.set_name("spotlight-list")
        self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        try:
            self.listbox.set_activate_on_single_click(True)
        except Exception:
            pass
        self.listbox.connect("row-activated", lambda _lb, row: self._on_row_activate(getattr(row, "_item", None) or self._row_item(row)))
        sc = Gtk.ScrolledWindow()
        sc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sc.set_max_content_height(300)
        sc.set_min_content_height(0)
        sc.add(self.listbox)
        box.pack_start(sc, True, True, 0)

        self.add(box)
        self.results = []
        self._initial_win_pos = None

        self.connect("key-press-event", self._on_key)
        self.entry.connect("key-press-event", self._on_entry_key)
        self.connect("focus-out-event", self._on_focus_out)
        self.show_all()
        self.hide()

    def show_spotlight(self):
        self.entry.set_text("")
        for w in list(self.listbox.get_children()):
            self.listbox.remove(w)
        self.results = []
        try:
            self.parent.modal_backdrop.hide()
        except Exception:
            pass
        fade_in(self)
        self.present()
        GLib.idle_add(lambda: (self.entry.grab_focus(), False)[1])

    def hide_spotlight(self):
        fade_out(self)

    def _on_focus_out(self, *_):
        if self.get_visible():
            self.hide_spotlight()
        return False

    def _on_change(self, text):
        q = (text or "").strip()
        for w in list(self.listbox.get_children()):
            self.listbox.remove(w)
        self.results = []
        if not q:
            return True
        ql = q.lower()
        titles = list(APPS.keys())
        app_matches = [t for t in titles if ql in t.lower()] or fuzzy_matches(q, titles, 0.3)
        for title in app_matches:
            self._append_result({"type": "app", "title": self._title(title), "payload": title})
        try:
            if os.path.isdir(DESKTOP_FOLDER):
                for f in sorted(os.listdir(DESKTOP_FOLDER), key=str.lower):
                    if ql in f.lower():
                        path = os.path.join(DESKTOP_FOLDER, f)
                        self._append_result({"type": "file", "title": f, "payload": path})
        except Exception:
            pass
        try:
            first = self.listbox.get_children()[0]
            self.listbox.select_row(first)
        except Exception:
            pass
        return False

    def _title(self, t):
        return {"FileManager": "Files"}.get(t, t)

    def _row_item(self, row):
        try:
            idx = list(self.listbox.get_children()).index(row)
        except Exception:
            idx = -1
        return self.results[idx] if 0 <= idx < len(self.results) else None

    def _append_result(self, item):
        row = Gtk.ListBoxRow()
        row._item = item
        h = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        h.set_margin_left(10)
        h.set_margin_right(10)
        h.set_margin_top(4)
        h.set_margin_bottom(4)
        if item["type"] == "app":
            icon = APPS.get(item["payload"], "application-x-executable")
            pb = pixbuf_from_ref(icon, 22)
            img = Gtk.Image.new_from_pixbuf(pb) if pb else load_icon(icon, 18)
            h.pack_start(img, False, False, 0)
        else:
            pb = pixbuf_from_ref("text-x-generic", 22)
            img = Gtk.Image.new_from_pixbuf(pb) if pb else load_icon("text-x-generic", 18)
            h.pack_start(img, False, False, 0)
        lbl = Gtk.Label(label=item["title"])
        lbl.set_xalign(0)
        lbl.set_ellipsize(Pango.EllipsizeMode.END)
        h.pack_start(lbl, True, True, 0)
        row.add(h)
        self.listbox.add(row)
        self.results.append(item)
        self.show_all()

    def _on_row_activate(self, item):
        if not item:
            return
        if item["type"] == "app":
            try:
                self.parent.open_app(item["payload"])
            except Exception:
                info_dialog(self, "Xato", f"{item['payload']} ochib bo'lmadi")
        else:
            self._open_file(item["payload"])
        self.hide_spotlight()

    def _open_file(self, path):
        try:
            try:
                self.parent.open_app(None, payload=path)
            except Exception:
                pass
        except Exception:
            try:
                subprocess.Popen(["xdg-open", path],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                info_dialog(self.parent, "Open file", path)

    def _activate_selected(self):
        sel = self.listbox.get_selected_row()
        if not sel:
            children = self.listbox.get_children()
            sel = children[0] if children else None
        if sel:
            item = getattr(sel, "_item", None)
            self._on_row_activate(item)

    def _on_key(self, win, ev):
        try:
            name = (Gdk.keyval_name(ev.keyval) or "").lower()
            if name == "escape":
                self.hide_spotlight()
                return True
        except Exception:
            pass
        return False

    def _on_entry_key(self, entry, ev):
        try:
            name = (Gdk.keyval_name(ev.keyval) or "").lower()
            if name == "escape":
                self.hide_spotlight()
                return True
            if name in ("down", "uperrow"):
                rows = self.listbox.get_children()
                if rows:
                    sel = self.listbox.get_selected_row()
                    cur = rows.index(sel) if sel in rows else -1
                    nxt = min(cur + 1, len(rows) - 1) if name == "down" else max(cur - 1, 0)
                    self.listbox.select_row(rows[nxt])
                    try:
                        rows[nxt].get_child().focus(Gtk.DirectionType.UP)
                    except Exception:
                        pass
                return True
        except Exception:
            pass
        return False


# ---------- Desktop ----------

class Desktop(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=APP_TITLE)
        self.set_default_size(1280, 800)
        try:
            settings = Gtk.Settings.get_default()
            if settings:
                settings.set_property("gtk-application-prefer-dark-theme", True)
        except Exception:
            pass
        self.set_position(Gtk.WindowPosition.CENTER)

        try:
            ensure_desktop_folder(DESKTOP_FOLDER)
            ensure_desktop_folder(os.path.dirname(STATE_FILE) or DESKTOP_FOLDER)
        except Exception:
            pass

        self.open_windows = []
        self._app_windows = {}
        self.desktop_items = {}
        self._dragging = None

        self.overlay = Gtk.Overlay()
        self.add(self.overlay)

        self.wallpaper = Wallpaper(DEFAULT_WALLPAPER)
        try:
            self.wallpaper.set_halign(Gtk.Align.FILL)
            self.wallpaper.set_valign(Gtk.Align.FILL)
        except Exception:
            pass
        self.overlay.add(self.wallpaper)

        self.icon_layer = Gtk.Fixed()
        self.icon_layer.set_name("icon-layer")
        self.overlay.add_overlay(self.icon_layer)

        try:
            targets = make_target_entries([("text/uri-list", 0, 0), ("text/plain", 0, 1)])
        except Exception:
            targets = [Gtk.TargetEntry.new("text/plain", 0, 0)]
        self.icon_layer.drag_dest_set(Gtk.DestDefaults.ALL, targets, Gdk.DragAction.COPY)
        self.icon_layer.connect("drag-data-received", self.on_drag_data_received)

        # dim backdrop sits above icons, below dock/top bar
        self.modal_backdrop = Gtk.EventBox()
        self.modal_backdrop.set_name("modal-backdrop")
        self.modal_backdrop.set_above_child(True)
        self.overlay.add_overlay(self.modal_backdrop)
        self.modal_backdrop.connect("button-press-event", lambda *_: self._hide_overlay_and_popups())
        self.modal_backdrop.hide()

        self._build_dock()
        self.overlay.add_overlay(self._dock_evt)

        # hot corner
        hot = Gtk.EventBox()
        hot.set_size_request(44, 44)
        hot.set_name("hot-corner")
        hot.set_above_child(True)
        hot.set_halign(Gtk.Align.END)
        hot.set_valign(Gtk.Align.START)
        hot.connect("button-press-event", lambda *_: self.toggle_launchpad())
        self.overlay.add_overlay(hot)

        self._create_top_bar()

        try:
            self.launchpad = Launchpad(self)
            self.spotlight = Spotlight(self)
        except Exception:
            self.launchpad = None
            self.spotlight = None

        self._parallax_target = (0.0, 0.0)
        GLib.timeout_add(30, self._parallax_step)

        self.add_events(
            Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.KEY_PRESS_MASK
        )
        self.connect("motion-notify-event", self._on_motion)
        self.connect("button-release-event", self._on_release)
        self.connect("key-press-event", self._on_key_press)
        self.connect("size-allocate", lambda *_: GLib.idle_add(self.wallpaper._resize_scaled))

        self.load_desktop_state()
        self.apply_css()

        try:
            if DEFAULT_WALLPAPER and os.path.exists(DEFAULT_WALLPAPER):
                lum = image_average_luminance(DEFAULT_WALLPAPER)
                self.apply_dark_icon_backdrop(lum > 0.6)
        except Exception:
            pass

        fade_in(self, 240)

    # ---------------- top bar ----------------
    def _create_top_bar(self):
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        bar.set_name("top-bar")
        bar.set_halign(Gtk.Align.FILL)
        bar.set_valign(Gtk.Align.START)
        try:
            bar.set_margin_top(8)
            bar.set_margin_start(10)
            bar.set_margin_end(10)
            bar.set_margin_bottom(0)
        except Exception:
            pass

        brand = Gtk.Label(label="DragonOS")
        brand.get_style_context().add_class("top-brand")
        event = Gtk.EventBox()
        event.add(brand)
        event.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        event.connect("button-press-event", self._on_system_menu)
        bar.pack_start(event, False, False, 2)

        space = Gtk.Box()
        bar.pack_start(space, True, True, 0)

        self.batt_lbl = Gtk.Label(label="")
        self.batt_lbl.get_style_context().add_class("top-chip")

        self.net_lbl = Gtk.Label(label="")
        self.net_lbl.get_style_context().add_class("top-chip")

        self.clock = Gtk.Label(label="")
        self.clock.get_style_context().add_class("top-clock")
        clock_click = Gtk.EventBox()
        clock_click.add(self.clock)
        clock_click.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        clock_click.connect("button-press-event", self._on_system_menu)
        clock_click.set_tooltip_text("System menu")

        bar.pack_start(self.net_lbl, False, False, 0)
        bar.pack_start(self.batt_lbl, False, False, 0)
        bar.pack_start(clock_click, False, False, 2)

        self.overlay.add_overlay(bar)

        GLib.timeout_add_seconds(1, self._update_clock)
        self._update_clock()
        self._update_status()
        GLib.timeout_add_seconds(30, self._update_status)

    def _update_clock(self):
        try:
            now = datetime.now()
            self.clock.set_text(now.strftime("%H:%M"))
            self.net_lbl.set_tooltip_text(now.strftime("%A, %d %B %Y"))
        except Exception:
            pass
        return True

    def _update_status(self):
        pct, charging = battery_state()
        net = network_state()
        try:
            if pct is None:
                self.batt_lbl.set_text("")
            else:
                arrow = "⚡" if charging else ""
                self.batt_lbl.set_text(f"{arrow}{pct}%")
                self.batt_lbl.set_tooltip_text("Charging" if charging else "On battery")
            net_name = {"wireless": "Wi-Fi", "wired": "Ethernet"}.get(net, "")
            self.net_lbl.set_text(net_name)
            self.net_lbl.set_tooltip_text(net_name or "No network")
        except Exception:
            pass
        return True

    def _on_system_menu(self, widget, event=None):
        menu = Gtk.Menu()

        def item(label, cb, icon=None):
            mi = Gtk.MenuItem(label=label)
            if cb:
                mi.connect("activate", cb)
            menu.append(mi)

        item("About DragonOS…", lambda *_: self._about_dialog())
        item("Spotlight search", lambda *_: self.toggle_spotlight())
        item("Launchpad", lambda *_: self.toggle_launchpad())
        menu.append(Gtk.SeparatorMenuItem())

        item("Lock screen", lambda *_: self._lock_screen())
        item("Log out…", lambda *_: self._logout())
        menu.append(Gtk.SeparatorMenuItem())
        item("Restart…", lambda *_: self._confirm_power("reboot", "Restart"))
        item("Power off…", lambda *_: self._confirm_power("off", "Power Off"))

        menu.show_all()
        try:
            menu.popup_at_pointer(event)
        except Exception:
            try:
                menu.popup(None, None, None, None, 0, Gtk.get_current_event_time() or 0)
            except Exception:
                pass
        return True

    def _about_dialog(self):
        dlg = Gtk.MessageDialog(transient_for=self, flags=0, message_type=Gtk.MessageType.INFO,
                                buttons=Gtk.ButtonsType.OK, text="DragonOS — Alpine Edition")
        dlg.format_secondary_text(
            "Beautiful & fast GTK3 desktop environment.\n\n"
            "Shortcuts:\n  Super+L — Launchpad\n  Super+Space — Spotlight\n"
            "  Ctrl+Alt+T — Terminal\n  Super+G — Snap grid\n"
            "  Double-click desktop icon — open\n  Right-click — menu")
        dlg.run()
        dlg.destroy()

    def _lock_screen(self):
        try:
            subprocess.Popen(["sh", "-c", "xdg-screensaver lock || loginctl lock-session"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

    def _logout(self):
        try:
            if self.get_application():
                self.get_application().quit()
        except Exception:
            pass

    def _confirm_power(self, action, label):
        dlg = Gtk.MessageDialog(transient_for=self, flags=0,
                                message_type=Gtk.MessageType.WARNING,
                                buttons=Gtk.ButtonsType.OK_CANCEL, text=f"{label} the system?")
        dlg.set_modal(True)
        dlg.format_secondary_text("Make sure unsaved work has been saved.")
        resp = dlg.run()
        dlg.destroy()
        if resp == Gtk.ResponseType.OK:
            if not _power_system("off" if action == "off" else "reboot"):
                info_dialog(self, "DragonOS",
                            f"Could not {label.lower()}.\n\nTry: doas { 'poweroff' if action=='off' else 'reboot' }")

    # ---------------- dock ----------------
    def _build_dock(self):
        evt = Gtk.EventBox()
        evt.set_name("dock-overlay")
        evt.set_halign(Gtk.Align.CENTER)
        evt.set_valign(Gtk.Align.END)
        try:
            evt.set_margin_bottom(12)
        except Exception:
            pass
        evt.add_events(Gdk.EventMask.POINTER_MOTION_MASK)

        dock = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        dock.set_name("slim-dock")
        dock.set_border_width(6)
        evt.add(dock)

        sep0 = Gtk.Box()
        sep0.get_style_context().add_class("dock-sep")
        dock.pack_start(sep0, False, False, 1)

        self.dock_items = {}

        lp_btn = Gtk.Button()
        lp_btn.set_relief(Gtk.ReliefStyle.NONE)
        lp_btn.get_style_context().add_class("dock-btn")
        lp_pb = pixbuf_from_ref("view-grid-symbolic", 40)
        lp_img = Gtk.Image.new_from_pixbuf(lp_pb) if lp_pb else load_icon("view-grid-symbolic", 26)
        lp_btn.set_image(lp_img)
        lp_btn.set_tooltip_text("Launchpad (Super+L)")
        lp_btn.connect("clicked", lambda *_: self.toggle_launchpad())
        dock.pack_start(lp_btn, False, False, 0)

        sep = Gtk.Box()
        sep.get_style_context().add_class("dock-sep")
        dock.pack_start(sep, False, False, 2)

        for title, icon in APPS.items():
            btn = Gtk.Button()
            btn.set_relief(Gtk.ReliefStyle.NONE)
            btn.get_style_context().add_class("dock-btn")

            pb = pixbuf_from_ref(icon, 44)
            img = Gtk.Image.new_from_pixbuf(pb) if pb else load_icon(icon, 30)
            btn.set_image(img)
            btn.set_tooltip_text(title)
            btn.connect("clicked", lambda _w, t=title: self._on_dock_click(t))

            v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            v.pack_start(btn, False, False, 0)
            dot_frame = Gtk.Box()
            dot_frame.set_size_request(40, 6)
            dot = Gtk.Image()
            dot.get_style_context().add_class("dock-dot")
            dot.set_pixel_size(6)
            dot_frame.pack_start(dot, True, False, 0)
            v.pack_start(dot_frame, False, False, 0)

            try:
                btn.set_size_request(44, 44)
            except Exception:
                pass

            dock.pack_start(v, False, False, 0)
            self.dock_items[title] = {"pb": pb, "img": img, "size": 44.0, "target": 44.0, "dot": dot, "btn": btn}

        evt.connect("motion-notify-event", self._dock_magnify)
        evt.connect("leave-notify-event", self._dock_restore)

        self._dock_evt = evt
        self._dock_box = dock

        GLib.timeout_add(16, self._dock_tick)

    def _on_dock_click(self, title):
        wins = self._app_windows.get(title)
        if wins:
            try:
                top = wins[-1]
                if top.get_visible():
                    top.present()
                    return
            except Exception:
                pass
        self.open_app(title)

    def _dock_magnify(self, widget, ev):
        try:
            for title, it in self.dock_items.items():
                btn = it["btn"]
                try:
                    cx = btn.translate_coordinates(widget, 0, 0)[0] + btn.get_allocated_width() / 2.0
                except Exception:
                    continue
                d = abs(ev.x - cx)
                if d > 110:
                    it["target"] = 44.0
                    continue
                t = 1.0 - d / 110.0
                it["target"] = 44.0 + 22.0 * t * t
        except Exception:
            pass

    def _dock_restore(self, *_):
        for it in self.dock_items.values():
            it["target"] = 44.0

    def _dock_tick(self):
        try:
            changed = any(abs(it["size"] - it["target"]) > 0.01 for it in self.dock_items.values())
            for it in self.dock_items.values():
                it["size"] += (it["target"] - it["size"]) * 0.35
                if changed:
                    s = int(round(max(16, it["size"])))
                    if it["pb"]:
                        try:
                            it["img"].set_from_pixbuf(it["pb"].scale_simple(s, s, GdkPixbuf.InterpType.BILINEAR))
                        except Exception:
                            pass
                    else:
                        try:
                            it["img"].set_pixel_size(s)
                        except Exception:
                            pass
                    try:
                        it["btn"].set_size_request(s, s)
                    except Exception:
                        pass
        except Exception:
            pass
        self._dock_counts()
        return True

    def _dock_counts(self):
        try:
            open_titles = {t for t, wins in self._app_windows.items() if wins}
            for title, it in self.dock_items.items():
                want = title in open_titles
                try:
                    if want:
                        it["dot"].set_pixel_size(6)
                        it["dot"].show_all()
                    else:
                        it["dot"].hide()
                except Exception:
                    pass
        except Exception:
            pass

    # ---------------- hide overlays ----------------
    def _hide_overlay_and_popups(self):
        try:
            lp = getattr(self, "launchpad", None)
            if lp is not None and lp.get_visible():
                lp.hide_lp()
            sp = getattr(self, "spotlight", None)
            if sp is not None and sp.get_visible():
                sp.hide_spotlight()
        except Exception:
            pass
        try:
            self.modal_backdrop.hide()
        except Exception:
            pass

    def toggle_launchpad(self):
        lp = getattr(self, "launchpad", None)
        if lp is None:
            return
        try:
            sp = getattr(self, "spotlight", None)
            if sp is not None and sp.get_visible():
                sp.hide_spotlight()
        except Exception:
            pass
        try:
            if lp.get_visible():
                lp.hide_lp()
                self.modal_backdrop.hide()
            else:
                self.modal_backdrop.show_all()
                lp.show_lp()
        except Exception:
            pass

    def toggle_spotlight(self):
        sp = getattr(self, "spotlight", None)
        if sp is None:
            return
        try:
            visible = sp.get_visible()
            if visible:
                sp.hide_spotlight()
            else:
                sp.show_spotlight()
        except Exception:
            pass

    # ---------------- window/app creation ----------------
    def _register_window(self, key, w):
        try:
            self.open_windows.append(w)
            self._app_windows.setdefault(key, []).append(w)
            w.connect("destroy", self._on_app_window_destroy, key)
        except Exception:
            pass

    def _on_app_window_destroy(self, w, key):
        try:
            if w in self.open_windows:
                self.open_windows.remove(w)
        except Exception:
            pass
        try:
            wins = self._app_windows.get(key, [])
            if w in wins:
                wins.remove(w)
        except Exception:
            pass

    def _make_window_for(self, widget, win_title=None, width=720, height=480, app_key=None):
        try:
            key = app_key or win_title or "App"
            if isinstance(widget, Gtk.Dialog):
                dlg = widget
                try:
                    dlg.set_transient_for(self)
                    dlg.set_modal(False)
                except Exception:
                    pass
                fade_in(dlg, 180)
                self._register_window(key, dlg)
                dlg.present()
                return dlg

            w = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
            w.set_transient_for(self)
            w.set_title(win_title)
            w.set_default_size(width, height)
            try:
                parent = widget.get_parent()
                if parent:
                    parent.remove(widget)
            except Exception:
                pass

            w.add(widget)
            fade_in(w, 180)
            self._register_window(key, w)
            w.present()
            return w
        except Exception as e:
            try:
                info_dialog(self, "Xato", f"Oyna yaratishda xato:\n{e}")
            except Exception:
                pass
            return None

    def open_app(self, title, payload=None):
        try:
            self.modal_backdrop.hide()
            if payload and title is None:
                if os.path.isdir(payload):
                    title = "FileManager"
                else:
                    _, ext = os.path.splitext(payload.lower())
                    if ext in TEXT_EXTS:
                        title = "Notepad"
                    elif ext in IMAGE_EXTS or ext in VIDEO_EXTS:
                        title = "Gallery"
                    else:
                        try:
                            if sys.platform.startswith("win"):
                                os.startfile(payload)
                            else:
                                subprocess.Popen(["xdg-open", payload],
                                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        except Exception:
                            pass
                        return

            if title == "Settings":
                dlg_obj = SettingsDialog(self)
                try:
                    if isinstance(dlg_obj, Gtk.Dialog):
                        dlg_obj.set_transient_for(self)
                        dlg_obj.set_modal(False)
                        fade_in(dlg_obj, 200)
                        self._register_window("Settings", dlg_obj)
                        dlg_obj.present()
                        return
                    if hasattr(dlg_obj, "run") and callable(getattr(dlg_obj, "run")):
                        try:
                            dlg_obj.run()
                        except Exception as e:
                            info_dialog(self, "Settings", f"Settings ochishda xato:\n{e}")
                        return
                    self._make_window_for(dlg_obj, win_title="Settings", width=680, height=520, app_key="Settings")
                    return
                except Exception as e:
                    info_dialog(self, "Settings", f"Settings ochishda xato:\n{e}")
                    return

            if title == "Terminal":
                self._open_terminal()
                return

            if title == "Notepad":
                nb = Notepad()
                if payload and os.path.exists(payload):
                    try:
                        with open(payload, "r", encoding="utf-8") as f:
                            data = f.read(200000)
                        try:
                            buf = nb.view.get_buffer()
                            buf.set_text(data)
                        except Exception:
                            if hasattr(nb, "load"):
                                nb.load(payload)
                    except Exception:
                        pass
                self._make_window_for(nb, win_title=(os.path.basename(payload) if payload else "Notepad"),
                                      width=760, height=540, app_key="Notepad")
                return

            if title == "Calculator":
                calc = Calculator()
                self._make_window_for(calc, win_title="Calculator", width=380, height=460, app_key="Calculator")
                return

            if title == "Gallery":
                gal = Gallery()
                if payload and os.path.exists(payload):
                    try:
                        if hasattr(gal, "load"):
                            gal.load(payload)
                        elif hasattr(gal, "open_path"):
                            gal.open_path(payload)
                    except Exception:
                        pass
                self._make_window_for(gal, win_title="Gallery", width=900, height=620, app_key="Gallery")
                return

            if title == "FileManager":
                fm = FileManager()
                if payload and os.path.exists(payload):
                    try:
                        if hasattr(fm, "open_path"):
                            fm.open_path(payload)
                        elif hasattr(fm, "set_path"):
                            fm.set_path(payload)
                    except Exception:
                        pass
                self._make_window_for(fm, win_title="Files", width=900, height=620, app_key="FileManager")
                return

            if title == "MusicPlayer":
                mp = MusicPlayer()
                self._make_window_for(mp, win_title="MusicPlayer", width=760, height=520, app_key="MusicPlayer")
                return

            if payload:
                if os.path.isdir(payload):
                    self.open_app("FileManager", payload=payload)
                    return
                if os.path.exists(payload):
                    _, ext = os.path.splitext(payload.lower())
                    if ext in TEXT_EXTS:
                        self.open_app("Notepad", payload=payload)
                        return
                    if ext in IMAGE_EXTS or ext in VIDEO_EXTS:
                        self.open_app("Gallery", payload=payload)
                        return
                    try:
                        subprocess.Popen(["xdg-open", payload],
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        return
                    except Exception:
                        pass

            info_dialog(self, "Xato", f"{title} topilmadi yoki qo'llab-quvvatlanmaydi")
        except Exception as e:
            try:
                info_dialog(self, "Xato", f"open_app ichida xato:\n{e}")
            except Exception:
                pass

    def _open_terminal(self):
        try:
            from Terminal import TermTab
            term = TermTab()
            try:
                view = getattr(getattr(term, "left", None), "text", None)
                if view:
                    view.set_name("term-view")
            except Exception:
                pass
            w = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
            w.set_transient_for(self)
            w.set_title("Terminal")
            w.set_default_size(820, 520)
            w.get_style_context().add_class("terminal-win")
            try:
                inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
                inner.add(term)
                w.add(inner)
            except Exception:
                w.add(term)
            fade_in(w, 160)
            self._register_window("Terminal", w)
            w.present()
        except Exception as e:
            info_dialog(self, "Terminal", f"Terminal ochib bo'lmadi:\n{e}")

    # ---------------- icon helpers ----------------
    def _get_icon_image_from_box(self, box):
        try:
            child = box.get_child()
            if child and isinstance(child, Gtk.Box):
                kids = child.get_children()
                if kids and isinstance(kids[0], Gtk.Image):
                    return kids[0]
        except Exception:
            pass
        return None

    def add_desktop_item(self, path, x=50, y=50, is_app=False, app_name=None):
        name = app_name if is_app else (os.path.basename(path) if path else "Unknown")
        box = Gtk.EventBox()
        box.set_name("desktop-icon")
        box.get_style_context().add_class("desktop-icon")

        if is_app and app_name:
            icon_ref = APPS.get(app_name, "application-x-executable")
        elif path and os.path.isdir(path):
            icon_ref = "folder"
        else:
            _, ext = os.path.splitext(str(path or "").lower())
            if ext in IMAGE_EXTS:
                icon_ref = "image-x-generic"
            elif ext in VIDEO_EXTS:
                icon_ref = "video-x-generic"
            else:
                icon_ref = "text-x-generic"

        pb = pixbuf_from_ref(icon_ref, ICON_SIZE)
        img = Gtk.Image.new_from_pixbuf(pb) if pb else load_icon(icon_ref, ICON_SIZE)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        vbox.set_halign(Gtk.Align.CENTER)
        vbox.set_valign(Gtk.Align.CENTER)
        vbox.pack_start(img, False, False, 0)

        lbl_text = name if len(name) <= 18 else name[:15] + "…"
        lbl = Gtk.Label(label=lbl_text)
        lbl.set_ellipsize(Pango.EllipsizeMode.END)
        lbl.set_width_chars(12)
        lbl.set_xalign(0.5)
        lbl.get_style_context().add_class("desktop-label")
        vbox.pack_start(lbl, False, False, 0)

        box.add(vbox)
        box._label = lbl
        box._icon_ref = icon_ref
        box._size = ICON_SIZE

        try:
            self.icon_layer.put(box, int(x), int(y))
        except Exception:
            try:
                self.icon_layer.put(box, 50, 50)
            except Exception:
                pass

        box.show_all()
        key = f"{name}-{uuid.uuid4().hex[:8]}"
        self.desktop_items[key] = (box, path, is_app, app_name)

        box.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.ENTER_NOTIFY_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK
        )
        box.connect("button-press-event", lambda wid, ev, k=key: self._on_icon_pressed(wid, ev, k))
        box.connect("button-release-event", lambda wid, ev, k=key: self._on_icon_released(wid, ev, k))

        self.save_desktop_state()
        return key

    # ---------------- dragging / opening ----------------
    def _on_icon_pressed(self, widget, event, key):
        if event.type == Gdk.EventType._2BUTTON_PRESS and event.button == 1:
            entry = self.desktop_items.get(key)
            if entry:
                self._open_entry(entry)
            self._dragging = None
            return True
        if event.type == Gdk.EventType.BUTTON_PRESS and event.button == 1:
            try:
                x, y = self.icon_layer.child_get_position(widget)
            except Exception:
                x, y = 0, 0
            self._dragging = {"key": key, "wid": widget,
                              "sx": int(event.x_root), "sy": int(event.y_root),
                              "ox": int(x), "oy": int(y), "moved": False}
            return True
        if event.type == Gdk.EventType.BUTTON_PRESS and event.button == 3:
            return self.on_desktop_item_click(widget, event, key)
        return False

    def _on_icon_released(self, widget, event, key):
        drag = self._dragging
        if not drag or drag.get("key") != key:
            return False
        try:
            cur_x, cur_y = self.icon_layer.child_get_position(widget)
            if SNAP_TO_GRID:
                nx = int(round(cur_x / GRID_SIZE)) * GRID_SIZE
                ny = int(round(cur_y / GRID_SIZE)) * GRID_SIZE
                try:
                    self.icon_layer.move(widget, nx, ny)
                except Exception:
                    self.icon_layer.put(widget, nx, ny)
        except Exception:
            pass
        self.save_desktop_state()
        self._dragging = None
        return True

    def _on_motion(self, win, ev):
        try:
            alloc = self.get_allocation()
            if alloc and alloc.width > 0 and alloc.height > 0:
                cx, cy = alloc.width / 2.0, alloc.height / 2.0
                self._parallax_target = (
                    (ev.x - cx) / max(1.0, cx) * 0.6,
                    (ev.y - cy) / max(1.0, cy) * 0.6,
                )
        except Exception:
            pass

        if not self._dragging:
            return False
        try:
            dx = int(ev.x_root - self._dragging["sx"])
            dy = int(ev.y_root - self._dragging["sy"])
            if abs(dx) + abs(dy) > 3:
                self._dragging["moved"] = True
            nx = max(0, self._dragging["ox"] + dx)
            ny = max(0, self._dragging["oy"] + dy)
            self.icon_layer.move(self._dragging["wid"], nx, ny)
        except Exception:
            pass
        return True

    def _parallax_step(self):
        try:
            curx, cury = self.wallpaper._parallax_offset
            tx, ty = self._parallax_target
            nx = curx + (tx - curx) * 0.18
            ny = cury + (ty - cury) * 0.18
            if abs(nx - curx) > 0.001 or abs(ny - cury) > 0.001:
                self.wallpaper.set_parallax_offset(nx, ny)
            else:
                self.wallpaper._parallax_offset = (nx, ny)
        except Exception:
            pass
        return True

    def _on_release(self, widget, event):
        if not getattr(self, "_dragging", None):
            return False
        drag = self._dragging
        try:
            wid = drag.get("wid")
            cur_x, cur_y = self.icon_layer.child_get_position(wid)
            if SNAP_TO_GRID:
                nx = int(round(cur_x / GRID_SIZE)) * GRID_SIZE
                ny = int(round(cur_y / GRID_SIZE)) * GRID_SIZE
                try:
                    self.icon_layer.move(wid, nx, ny)
                except Exception:
                    self.icon_layer.put(wid, nx, ny)
        except Exception:
            pass
        try:
            self.save_desktop_state()
        except Exception:
            pass
        self._dragging = None
        return True

    # ---------------- context menu & file ops ----------------
    def on_desktop_item_click(self, widget, event, key):
        entry = self.desktop_items.get(key)
        if not entry:
            return False
        box, target, is_app, app_name = entry

        menu = Gtk.Menu()

        open_item = Gtk.MenuItem(label="Open")
        open_item.connect("activate", lambda *_: self._open_entry(entry))
        menu.append(open_item)

        if not is_app and os.path.exists(target):
            _, ext = os.path.splitext(target.lower())
            if ext in TEXT_EXTS:
                menu.append(self._mk_menu("Open in Notepad",
                                          lambda *_: self.open_app("Notepad", payload=target)))
            if ext in IMAGE_EXTS or ext in VIDEO_EXTS:
                menu.append(self._mk_menu("Open in Gallery",
                                          lambda *_: self.open_app("Gallery", payload=target)))

        menu.append(Gtk.SeparatorMenuItem())

        menu.append(self._mk_menu("Copy path", self._copy_clipboard(target)))
        menu.append(self._mk_menu(
            "Reveal in Files", self._reveal_in_files(target)))
        menu.append(self._mk_menu("Rename", lambda *_: self._rename_entry(key)))

        if not is_app and os.path.exists(target):
            _, ext = os.path.splitext(target.lower())
            if ext in IMAGE_EXTS:
                menu.append(self._mk_menu("Set as wallpaper",
                                          lambda *_: self._set_wallpaper_from_path(target)))

        menu.append(Gtk.SeparatorMenuItem())
        menu.append(self._mk_menu("Properties", lambda *_: self._show_properties(entry)))
        menu.append(self._mk_menu("Remove", lambda *_: self.remove_desktop_item(key)))

        menu.show_all()
        try:
            menu.popup_at_pointer(event)
        except Exception:
            menu.popup(None, None, None, None, event.button, event.time)
        return True

    def _mk_menu(self, label, cb):
        mi = Gtk.MenuItem(label=label)
        mi.connect("activate", cb)
        return mi

    def _copy_clipboard(self, target):
        def cb(_item):
            try:
                Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(str(target or ""), -1)
            except Exception:
                pass
        return cb

    def _reveal_in_files(self, target):
        def cb(_item):
            try:
                folder = target if os.path.isdir(target) else os.path.dirname(target)
                self.open_app("FileManager", payload=folder)
            except Exception as e:
                info_dialog(self, "Xato", f"Papkani ochib bo'lmadi: {e}")
        return cb

    def _open_entry(self, entry):
        box, target, is_app, app_name = entry
        if is_app and app_name:
            self.open_app(app_name)
            return
        if not target:
            return
        if os.path.isdir(target):
            self.open_app("FileManager", payload=target)
            return
        if not os.path.exists(target):
            info_dialog(self, "Xato", "Fayl topilmadi")
            return
        _, ext = os.path.splitext(target.lower())
        if ext in TEXT_EXTS:
            self.open_app("Notepad", payload=target)
            return
        if ext in IMAGE_EXTS or ext in VIDEO_EXTS:
            self.open_app("Gallery", payload=target)
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(target)
            else:
                subprocess.Popen(["xdg-open", target],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            info_dialog(self, "Xato", f"Faylni ochishda xato: {target}")

    def _rename_entry(self, key):
        entry = self.desktop_items.get(key)
        if not entry:
            return
        old_name = os.path.basename(entry[1]) if entry[1] else ""
        dlg = Gtk.Dialog("Rename", transient_for=self, flags=0)
        dlg.add_button(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL)
        dlg.add_button(Gtk.STOCK_OK, Gtk.ResponseType.OK)
        dlg.set_default_size(380, 120)
        dlg.set_modal(True)
        box = dlg.get_content_area()
        box.set_border_width(12)
        e = Gtk.Entry()
        e.set_text(old_name)
        e.set_activates_default(True)
        box.add(e)
        ok = dlg.get_widget_for_response(Gtk.ResponseType.OK)
        if ok:
            dialog_default = ok.get_can_default()
        dlg.show_all()
        try:
            dlg.set_default(ok)
        except Exception:
            pass
        if dlg.run() == Gtk.ResponseType.OK:
            newname = e.get_text().strip()
            if newname:
                try:
                    newpath = os.path.join(os.path.dirname(entry[1] or DESKTOP_FOLDER), newname)
                    os.rename(entry[1], newpath)
                    self.desktop_items[key] = (entry[0], newpath, entry[2], entry[3])
                    try:
                        entry[0]._label.set_text(newname[:18])
                    except Exception:
                        pass
                    self.save_desktop_state()
                except Exception as ex:
                    info_dialog(self, "Xato", f"Rename bo'lmadi:\n{ex}")
        dlg.destroy()

    def _show_properties(self, entry):
        box, target, is_app, app_name = entry
        try:
            st = os.stat(target) if target and os.path.exists(target) else None
            dlg = Gtk.MessageDialog(transient_for=self, flags=0, message_type=Gtk.MessageType.INFO,
                                    buttons=Gtk.ButtonsType.OK,
                                    text=os.path.basename(target) if target else "Unknown")
            body = [f"Path: {target}"]
            if st:
                sz = st.st_size
                human = f"{sz/1024:.1f} KB" if sz < 1024 * 1024 else f"{sz/1024/1024:.2f} MB"
                body += [f"Size: {sz} bytes ({human})", f"Mode: {oct(st.st_mode)}"]
            dlg.format_secondary_text("\n".join(body))
            dlg.run()
            dlg.destroy()
        except Exception:
            info_dialog(self, "Xato", "Properties ko'rsatilmadi")

    def remove_desktop_item(self, key):
        entry = self.desktop_items.get(key)
        if not entry:
            return
        widget = entry[0]
        try:
            self.icon_layer.remove(widget)
        except Exception:
            pass
        try:
            del self.desktop_items[key]
        except Exception:
            pass
        self.save_desktop_state()

    def _set_wallpaper_from_path(self, path):
        try:
            self.wallpaper.set_wallpaper(path)
            self.save_desktop_state()
        except Exception as e:
            info_dialog(self, "Xato", f"Wallpaper o'rnatib bo'lmadi:\n{e}")

    # ---------------- drag & drop ----------------
    def on_drag_data_received(self, widget, drag_context, x, y, selection_data, info, time):
        try:
            try:
                uris = selection_data.get_uris()
            except Exception:
                uris = None
            if uris and len(uris) > 0:
                u = uris[0]
                try:
                    path = GLib.filename_from_uri(u)[0]
                except Exception:
                    path = u[7:] if u.startswith("file://") else u
                path = path.rstrip("\r\n")
                if os.path.exists(path):
                    ensure_desktop_folder(DESKTOP_FOLDER)
                    dest_name = os.path.basename(path)
                    dest = os.path.join(DESKTOP_FOLDER, dest_name)
                    i = 0
                    base = dest
                    while os.path.exists(dest):
                        i += 1
                        dest = f"{base}.{i}"
                    try:
                        try:
                            os.symlink(path, dest)
                        except (OSError, NotImplementedError):
                            shutil.copy2(path, dest)
                        self.add_desktop_item(dest, x, y, is_app=False)
                        drag_context.finish(True, False, time)
                        return
                    except Exception:
                        info_dialog(self, "Xato", "Desktop-ga qo'yib bo'lmadi")
                        drag_context.finish(False, False, time)
                        return
            try:
                text = selection_data.get_text()
            except Exception:
                text = None
            if text:
                text = text.strip()
                if text in APPS:
                    self.add_desktop_item(text, x, y, is_app=True, app_name=text)
                    drag_context.finish(True, False, time)
                    return
                if os.path.exists(text):
                    self.add_desktop_item(text, x, y, is_app=False)
                    drag_context.finish(True, False, time)
                    return
        except Exception:
            pass
        drag_context.finish(False, False, time)

    # ---------------- persistence ----------------
    def save_desktop_state(self):
        try:
            ensure_desktop_folder(os.path.dirname(STATE_FILE) or DESKTOP_FOLDER)
            items = []
            for k, v in list(self.desktop_items.items()):
                widget = v[0]
                try:
                    x, y = self.icon_layer.child_get_position(widget)
                except Exception:
                    x, y = 50, 50
                items.append({"path": v[1], "is_app": bool(v[2]), "app_name": v[3],
                              "x": int(x), "y": int(y)})
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump({"items": items}, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def load_desktop_state(self):
        try:
            if os.path.exists(STATE_FILE):
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for it in data.get("items", []):
                    self.add_desktop_item(it.get("path"), it.get("x", 50), it.get("y", 50),
                                          is_app=it.get("is_app", False),
                                          app_name=it.get("app_name"))
        except Exception:
            pass

    # ---------------- styling & keyboard ----------------
    def apply_css(self):
        css = """
        /* ==== DragonOS glass theme (Alpine Edition) ==== */
        window, #desktop-root { background: #0b0e13; }

        #wallpaper { background: transparent; }

        #modal-backdrop {
          background: rgba(6, 8, 12, 0.55);
        }

        /* Top bar */
        #top-bar {
          padding: 2px 4px;
          background: rgba(13, 16, 24, 0.60);
          border-radius: 12px;
          border: 1px solid rgba(255,255,255,0.09);
          box-shadow: inset 0 1px 0 rgba(255,255,255,0.06);
          transition: background 150ms ease-out;
        }
        #top-bar:hover { background: rgba(15, 19, 28, 0.72); }
        .top-brand {
          color: rgba(255,255,255,0.95);
          font-weight: 700;
          font-size: 12px;
          padding: 0 8px;
        }
        .top-clock {
          color: #ffffff;
          font-weight: 600;
          font-size: 12px;
          padding: 3px 8px;
        }
        .top-chip {
          color: rgba(235,242,255,0.88);
          font-size: 11px;
          font-weight: 500;
          padding: 3px 8px;
        }

        /* Dock */
        #slim-dock {
          background: rgba(15, 18, 27, 0.62);
          border-radius: 18px;
          border: 1px solid rgba(255,255,255,0.10);
          box-shadow: inset 0 1px 0 rgba(255,255,255,0.08);
        }
        #dock-overlay { background: transparent; padding: 6px; }
        .dock-btn { border-radius: 12px; background: transparent; padding: 5px; transition: all 120ms ease-out; }
        .dock-btn:hover { background: rgba(255,255,255,0.10); }
        .dock-btn:active { background: rgba(124,140,255,0.25); }
        .dock-sep {
          min-width: 2px; min-height: 30px; margin-top: 8px;
          background: rgba(255,255,255,0.12); border-radius: 1px;
        }
        .dock-dot { color: #7c8cff; background: #7c8cff; border-radius: 3px; }

        /* Desktop icons */
        #desktop-icon {
          border: 0;
          padding: 6px; margin: 4px;
          background: rgba(0,0,0,0.16);
          border-radius: 12px;
          transition: background 120ms ease-out;
        }
        #desktop-icon:hover { background: rgba(124,140,255,0.18); }
        .desktop-label {
          color: #ffffff;
          font-size: 11px;
          font-weight: 500;
          margin-top: 2px;
          text-shadow: none;
        }

        /* Launchpad */
        #launchpad {
          background: rgba(11, 14, 20, 0.90);
          border-radius: 22px;
          border: 1px solid rgba(255,255,255,0.10);
        }
        #lp-search {
          background: rgba(255,255,255,0.08);
          color: #ffffff;
          border-radius: 999px;
          padding: 8px 14px;
          font-size: 13px;
          border: 1px solid rgba(255,255,255,0.08);
          caret-color: #7c8cff;
        }
        #lp-search:focus { background: rgba(255,255,255,0.12); }
        #lp-sc, #lp-grid { background: transparent; }
        .lp-tile {
          border-radius: 18px;
          padding: 14px 8px 10px 8px;
          min-width: 96px;
          background: rgba(255,255,255,0.03);
          border: 1px solid rgba(255,255,255,0.05);
          transition: all 130ms ease-out;
        }
        .lp-tile:hover {
          background: rgba(124,140,255,0.16);
          border-color: rgba(124,140,255,0.25);
        }
        .lp-tile:active { background: rgba(124,140,255,0.30); }
        #lp-grid flowboxchild { border-radius: 18px; background: transparent; }
        #lp-grid flowboxchild:selected { background: rgba(124,140,255,0.22); }
        .lp-label { color: rgba(255,255,255,0.92); font-size: 11px; font-weight: 500; }

        /* Spotlight */
        #spotlight {
          background: rgba(13, 16, 23, 0.92);
          border-radius: 16px;
          border: 1px solid rgba(255,255,255,0.12);
        }
        #spotlight-entry {
          background: rgba(255,255,255,0.07);
          color: #ffffff;
          border-radius: 10px;
          padding: 10px 12px;
          font-size: 14px;
          border: 1px solid rgba(255,255,255,0.08);
          caret-color: #7c8cff;
        }
        #spotlight-entry:focus { background: rgba(255,255,255,0.11); }
        #spotlight-list { background: transparent; }
        #spotlight-list row {
          background: transparent; border-radius: 10px;
          color: #e8eefc; font-size: 12px;
          transition: background 100ms;
        }
        #spotlight-list row:hover { background: rgba(255,255,255,0.06); }
        #spotlight-list row:selected { background: rgba(124,140,255,0.28); }

        #hot-corner { background: transparent; }

        /* Terminal window */
        .terminal-win { background: #0d1117; }
        #term-view, #term-view text {
          background: #0d1117;
          color: #e6edf3;
          font-family: "SFMono-Regular", "DejaVu Sans Mono", monospace;
          font-size: 12px;
        }

        /* Windows & dialogs */
        dialog { background: #12151c; color: #e8eefc; }
        messagedialog { border-radius: 14px; }
        button {
          background: rgba(255,255,255,0.05);
          color: #e8eefc;
          border-radius: 10px;
          border: 1px solid rgba(255,255,255,0.06);
          padding: 7px 12px;
          transition: all 120ms ease-out;
        }
        button:hover { background: rgba(255,255,255,0.10); }
        button:active { background: rgba(124,140,255,0.28); }
        entry {
          background: rgba(255,255,255,0.07);
          color: #ffffff;
          border-radius: 10px;
          padding: 8px 10px;
          border: 1px solid rgba(255,255,255,0.06);
          caret-color: #7c8cff;
        }
        entry:focus { background: rgba(255,255,255,0.11); }
        combobox { color: #e8eefc; }
        label { color: #dfe7f3; }
        frame { border-radius: 12px; }
        scrollbar { background: transparent; }
        scrollbar slider {
          background: rgba(255,255,255,0.22);
          border-radius: 8px;
          min-width: 6px;
          min-height: 24px;
        }
        """
        try:
            prov = Gtk.CssProvider()
            prov.load_from_data(css.encode("utf-8"))
            screen = Gdk.Screen.get_default()
            Gtk.StyleContext.add_provider_for_screen(screen, prov, Gtk.STYLE_PROVIDER_PRIORITY_USER)
        except Exception:
            pass

    def apply_dark_icon_backdrop(self, enable=True):
        try:
            if enable:
                css = "#desktop-icon { background: rgba(0,0,0,0.18); padding:6px; border-radius:12px; }"
            else:
                css = "#desktop-icon { background: transparent; padding: 0; }"
            prov = Gtk.CssProvider()
            prov.load_from_data(css.encode("utf-8"))
            screen = Gdk.Screen.get_default()
            Gtk.StyleContext.add_provider_for_screen(screen, prov, Gtk.STYLE_PROVIDER_PRIORITY_USER)
        except Exception:
            pass

    def toggle_dark(self, on=True):
        try:
            Gtk.Settings.get_default().set_property("gtk-application-prefer-dark-theme", bool(on))
        except Exception:
            pass

    def _on_key_press(self, widget, ev):
        name = (Gdk.keyval_name(ev.keyval) or "").lower()
        state = ev.state
        try:
            super_mask = (Gdk.ModifierType.SUPER_MASK | Gdk.ModifierType.MOD4_MASK | Gdk.ModifierType.META_MASK)
            is_super = bool(state & super_mask)
            ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
            alt = bool(state & Gdk.ModifierType.MOD1_MASK)

            if name == "g" and is_super:
                global SNAP_TO_GRID
                SNAP_TO_GRID = not SNAP_TO_GRID
                info_dialog(self, "Grid", f"Snap-to-grid: {SNAP_TO_GRID}")
                return True
            if name == "space" and is_super:
                self.toggle_spotlight()
                return True
            if name in ("l", "a") and is_super:
                self.toggle_launchpad()
                return True
            if name == "t" and ctrl and alt:
                self._open_terminal()
                return True
            if name == "escape":
                self._hide_overlay_and_popups()
                return True
        except Exception:
            pass
        return False


__all__ = ["Desktop", "DESKTOP_FOLDER", "Wallpaper", "Launchpad", "Spotlight"]
