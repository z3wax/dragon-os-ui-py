# Ultra-minimal DragonOS desktop UI (GTK3) - enhanced (fixed)
import gi
import os
import json
import stat
import sys
import shutil
import difflib
import uuid

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib, Pango

from helpers import make_target_entries, info_dialog, load_icon, ensure_desktop_folder
from apps import Notepad, Calculator, Gallery, FileManager, MusicPlayer, SettingsDialog

APP_TITLE = "DragonOS"
DESKTOP_FOLDER = os.path.expanduser("~/Desktop")
STATE_FILE = os.path.join(DESKTOP_FOLDER, ".desktop_state.json")
# fixed wallpaper filename checks
DEFAULT_WALLPAPER = os.path.abspath("wallpaper.png") if os.path.exists("wallpaper.png") else None

APPS = {
    "Notepad": "accessories-text-editor",
    "Calculator": "accessories-calculator",
    "Gallery": "media-photo-manager",
    "FileManager": "folder",
    "MusicPlayer": "media-playback-start",
    "Settings": "emblem-system",
}

ICON_SIZE = 56
SNAP_TO_GRID = True
GRID_SIZE = 56

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
        h = small.get_height()
        w = small.get_width()
        # pixels is a bytes-like object; indexing returns ints in Python 3
        for yy in range(h):
            row_off = yy * stride
            for xx in range(w):
                off = row_off + xx * ch
                # guard bounds
                if off + 2 < len(pixels):
                    r = pixels[off]
                    g = pixels[off + 1]
                    b = pixels[off + 2]
                    lum = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0
                    total += lum
                    cnt += 1
        return (total / cnt) if cnt else 0.5
    except Exception:
        return 0.5


# fuzzy matching helper
def fuzzy_matches(query, choices, cutoff=0.45):
    q = (query or "").lower()
    if not q:
        return []
    scores = [(difflib.SequenceMatcher(None, q, c.lower()).ratio(), c) for c in choices]
    return [c for s, c in sorted(scores, reverse=True) if s >= cutoff]


# fade helpers for simple animations
def fade_in(win, duration=180):
    steps = 12
    step_ms = max(1, int(duration / steps))

    def step(i=[0]):
        try:
            i[0] += 1
            win.set_opacity(min(1.0, i[0] / steps))
            win.show_all()
            if i[0] >= steps:
                return False
            return True
        except Exception:
            return False

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

    def step(i=[0]):
        try:
            i[0] += 1
            win.set_opacity(max(0.0, 1.0 - i[0] / steps))
            if i[0] >= steps:
                try:
                    win.hide()
                except Exception:
                    pass
                win.set_opacity(1.0)
                return False
            return True
        except Exception:
            return False

    try:
        GLib.timeout_add(step_ms, step)
    except Exception:
        try:
            win.hide()
        except Exception:
            pass


# ---------- Wallpaper ----------
# wallpaper.py
# Robust Wallpaper widget for GTK3 (EventBox wrapper + parallax)
import os
from gi.repository import Gtk, GdkPixbuf, GLib

class Wallpaper(Gtk.EventBox):
    """
    Wallpaper widget:
      - uses Gtk.Image to show a scaled/cropped background image
      - set_visible_window(False) so it doesn't steal input events
      - optional parallax: set_parallax(True/False)
      - set_parallax_offset(nx, ny) accepts values in [-1.0 .. 1.0]
      - set_wallpaper(path) to load an image file
    """

    def __init__(self, path: str | None = None, parallax: bool = True, interactive: bool = False):
        super().__init__()

        # Do not create separate window for the EventBox (so it won't steal events)
        try:
            self.set_visible_window(False)
        except Exception:
            pass

        # Make it non-interactive by default so clicks reach icons above
        if not interactive:
            try:
                self.set_sensitive(False)
            except Exception:
                pass

        self.image = Gtk.Image()
        self.add(self.image)

        self._pb = None           # original GdkPixbuf of wallpaper
        self._path = None         # path to current wallpaper
        self._parallax_enabled = bool(parallax)
        self._parallax_offset = (0.0, 0.0)   # (px, py) in [-1.0,1.0]

        if path:
            self.set_wallpaper(path)

    # ---------------- public API ----------------
    def set_parallax(self, enabled: bool = True):
        self._parallax_enabled = bool(enabled)

    def set_parallax_offset(self, nx: float, ny: float):
        """
        nx, ny in [-1.0 .. 1.0] — relative offset for subtle parallax shift.
        Calls update to re-render immediately (if allocation available).
        """
        try:
            nx = max(-1.0, min(1.0, float(nx)))
            ny = max(-1.0, min(1.0, float(ny)))
            self._parallax_offset = (nx, ny)
            alloc = self.get_allocation()
            if alloc and alloc.width > 0 and alloc.height > 0:
                # schedule update in idle to avoid re-entrancy issues
                GLib.idle_add(self._update, alloc)
        except Exception:
            pass

    def set_wallpaper(self, path: str | None):
        """Set wallpaper to given filesystem path (or None to clear)."""
        try:
            if not path or not os.path.exists(path):
                self._path = None
                self._pb = None
                # fallback icon
                try:
                    self.image.set_from_icon_name("applications-graphics", Gtk.IconSize.DIALOG)
                except Exception:
                    self.image.clear()
                return

            self._path = path
            # load pixbuf (may raise)
            pb = GdkPixbuf.Pixbuf.new_from_file(path)
            self._pb = pb
            alloc = self.get_allocation()
            if alloc and alloc.width > 0 and alloc.height > 0:
                GLib.idle_add(self._update, alloc)
            else:
                # schedule update later when allocation is known
                GLib.idle_add(self._update, self.get_allocation())
        except Exception:
            # on any load failure, clear
            self._pb = None
            try:
                self.image.set_from_icon_name("image-missing", Gtk.IconSize.DIALOG)
            except Exception:
                self.image.clear()

    # ---------------- Gtk size handling ----------------
    def do_size_allocate(self, allocation):
        # call parent's allocator
        try:
            Gtk.EventBox.do_size_allocate(self, allocation)
        except Exception:
            pass
        # schedule update so scaling happens off the main reflow
        try:
            if self._pb:
                GLib.idle_add(self._update, allocation)
        except Exception:
            pass

    # ---------------- internal update/render ----------------
    def _update(self, allocation=None):
        """
        Render the wallpaper into the current allocation.
        This can be called by GLib.idle_add with an allocation argument.
        """
        try:
            alloc = allocation if allocation else self.get_allocation()
            if not alloc or alloc.width <= 0 or alloc.height <= 0:
                return False

            if not self._pb:
                return False

            pb = self._pb
            w, h = alloc.width, alloc.height

            # compute scale to cover the allocation (crop)
            scale_x = w / max(1, pb.get_width())
            scale_y = h / max(1, pb.get_height())
            scale = max(scale_x, scale_y)

            nw = max(1, int(round(pb.get_width() * scale)))
            nh = max(1, int(round(pb.get_height() * scale)))

            # scale the pixbuf
            try:
                scaled = pb.scale_simple(nw, nh, GdkPixbuf.InterpType.BILINEAR)
            except Exception:
                # fallback: use original if scaling fails
                scaled = pb

            # base center offsets
            base_x = max(0, (nw - w) // 2)
            base_y = max(0, (nh - h) // 2)

            if self._parallax_enabled:
                px, py = self._parallax_offset
                # amount of shift is a small fraction of image size (not allocation)
                # Using fraction of scaled image gives consistent subtle parallax
                max_x = max(1, int(nw * 0.06))
                max_y = max(1, int(nh * 0.06))
                ox = base_x + int(px * max_x)
                oy = base_y + int(py * max_y)
                # clamp so we don't go out of bounds
                ox = max(0, min(nw - w, ox))
                oy = max(0, min(nh - h, oy))
            else:
                ox, oy = base_x, base_y

            # create subpixbuf (crop)
            try:
                cropped = scaled.new_subpixbuf(ox, oy, min(w, nw), min(h, nh))
                self.image.set_from_pixbuf(cropped)
            except Exception:
                # final fallback: set the scaled full image (it may be larger)
                try:
                    self.image.set_from_pixbuf(scaled)
                except Exception:
                    try:
                        self.image.set_from_icon_name("image-missing", Gtk.IconSize.DIALOG)
                    except Exception:
                        self.image.clear()

        except Exception:
            # ignore errors silently to avoid UI crashes
            pass

        # return False if called via idle_add (one-shot)
        return False


# ---------- Launchpad (with search) ----------
class Launchpad(Gtk.Window):
    def __init__(self, parent):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.parent = parent
        self.set_transient_for(parent)
        self.set_modal(True)
        self.set_decorated(False)
        self.set_default_size(520, 420)
        self.set_position(Gtk.WindowPosition.CENTER)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_border_width(12)

        # search entry
        self.search = Gtk.Entry()
        self.search.set_placeholder_text("Search apps...")
        self.search.connect("changed", lambda e: self.populate(e.get_text()))
        box.pack_start(self.search, False, False, 0)

        sc = Gtk.ScrolledWindow()
        sc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        grid = Gtk.FlowBox()
        grid.set_max_children_per_line(6)
        grid.set_selection_mode(Gtk.SelectionMode.NONE)
        self._grid = grid
        sc.add(grid)
        box.pack_start(sc, True, True, 0)

        self.add(box)

        # handlers for escape / focus-out
        self.connect("key-press-event", self._on_key)
        self.connect("focus-out-event", lambda w, e: self.hide())

        self.populate()

    def populate(self, filter_text=""):
        # rebuild grid with optional filter (fuzzy)
        for c in list(self._grid.get_children()):
            self._grid.remove(c)
        ft = (filter_text or "").strip()
        titles = list(APPS.keys())
        if ft:
            # use fuzzy matches first, fallback to substring
            matches = fuzzy_matches(ft, titles, cutoff=0.35)
            if not matches:
                matches = [t for t in titles if ft.lower() in t.lower()]
        else:
            matches = titles
        for title in matches:
            icon = APPS.get(title, "application-x-executable")
            btn = Gtk.Button()
            btn.set_relief(Gtk.ReliefStyle.NONE)
            btn.get_style_context().add_class("lp-tile")
            img = load_icon(icon, size=56)
            # label + icon
            v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            v.pack_start(img, False, False, 0)
            lbl = Gtk.Label(label=title)
            lbl.set_xalign(0.5)
            v.pack_start(lbl, False, False, 0)
            btn.add(v)
            btn.set_tooltip_text(title)
            btn.connect("clicked", lambda _w, t=title: self._on_launch(t))
            self._grid.add(btn)
        self.show_all()

    def _on_launch(self, t):
        self.hide()
        try:
            self.parent.open_app(t)
        except Exception:
            info_dialog(self, "Xato", f"{t} ochib bo'lmadi")

    def _on_key(self, win, ev):
        try:
            name = Gdk.keyval_name(ev.keyval)
            if name and name.lower() == "escape":
                self.hide()
                return True
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
        GLib.idle_add(lambda: (self.search.grab_focus(), False))

    def hide_lp(self):
        fade_out(self)


# ---------- Spotlight search (Super+Space) ----------
class Spotlight(Gtk.Window):
    def __init__(self, parent):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.parent = parent
        self.set_transient_for(parent)
        self.set_decorated(False)
        self.set_modal(False)
        self.set_default_size(640, 120)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_resizable(False)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_border_width(10)

        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text("Search apps and Desktop files...")
        self.entry.connect("changed", lambda e: self._on_change(e.get_text()))
        self.entry.connect("activate", lambda e: self._activate_selected())
        box.pack_start(self.entry, False, False, 0)

        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        sc = Gtk.ScrolledWindow()
        sc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sc.set_min_content_height(180)
        sc.add(self.listbox)
        box.pack_start(sc, True, True, 0)

        self.add(box)
        self.results = []

        self.connect("key-press-event", self._on_key)
        self.entry.connect("key-press-event", self._on_entry_key)
        self.connect("focus-out-event", lambda w, e: self.hide())

        self.show_all()

    def show_spotlight(self):
        self.entry.set_text("")
        for w in list(self.listbox.get_children()):
            self.listbox.remove(w)
        self.results = []
        fade_in(self)
        self.present()
        GLib.idle_add(lambda: (self.entry.grab_focus(), False))

    def hide_spotlight(self):
        fade_out(self)

    def _on_change(self, text):
        q = (text or "").strip()
        for w in list(self.listbox.get_children()):
            self.listbox.remove(w)
        self.results = []
        if not q:
            return
        ql = q.lower()
        # search apps (substring + fuzzy)
        for title in APPS.keys():
            if ql in title.lower():
                self._append_result({"type": "app", "title": title, "payload": title})
        # desktop files
        try:
            if os.path.isdir(DESKTOP_FOLDER):
                for f in sorted(os.listdir(DESKTOP_FOLDER), key=lambda x: x.lower()):
                    if ql in f.lower():
                        path = os.path.join(DESKTOP_FOLDER, f)
                        self._append_result({"type": "file", "title": f, "payload": path})
        except Exception:
            pass

    def _append_result(self, item):
        row = Gtk.ListBoxRow()
        h = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        lbl = Gtk.Label(label=item["title"])
        lbl.set_xalign(0)
        h.pack_start(lbl, True, True, 0)
        row.add(h)
        row.connect("button-press-event", lambda w, e, it=item: self._on_row_activate(it) if e.type == Gdk.EventType.BUTTON_PRESS and e.button == 1 else None)
        self.listbox.add(row)
        self.results.append(item)
        self.show_all()

    def _on_row_activate(self, item):
        if item["type"] == "app":
            try:
                self.parent.open_app(item["payload"])
            except Exception:
                info_dialog(self, "Xato", f"{item['payload']} ochib bo'lmadi")
        else:
            path = item["payload"]
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = f.read(20000)
                dlg = Gtk.Dialog(title=os.path.basename(path), transient_for=self.parent, flags=0)
                dlg.add_button(Gtk.STOCK_CLOSE, Gtk.ResponseType.CLOSE)
                dlg.set_default_size(720, 480)
                nb = Notepad()
                try:
                    buf = nb.view.get_buffer()
                    buf.set_text(data)
                except Exception:
                    pass
                box = dlg.get_content_area()
                box.add(nb)
                dlg.show_all(); dlg.run(); dlg.destroy()
            except Exception:
                info_dialog(self.parent, "Open file", f"{path}")

    def _activate_selected(self):
        sel = self.listbox.get_selected_row()
        if sel:
            idx = list(self.listbox.get_children()).index(sel)
            if 0 <= idx < len(self.results):
                self._on_row_activate(self.results[idx])
                self.hide()

    def _on_key(self, win, ev):
        try:
            name = Gdk.keyval_name(ev.keyval)
            if name and name.lower() == "escape":
                self.hide()
                return True
        except Exception:
            pass
        return False

    def _on_entry_key(self, entry, ev):
        try:
            name = Gdk.keyval_name(ev.keyval)
            if name and name.lower() == "escape":
                self.hide()
                return True
        except Exception:
            pass
        return False


# ---------- Desktop ----------
class Desktop(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=APP_TITLE)
        self.set_default_size(1280, 800)
        self.set_position(Gtk.WindowPosition.CENTER)

        # Ensure folders exist
        try:
            ensure_desktop_folder(DESKTOP_FOLDER)
            ensure_desktop_folder(os.path.dirname(STATE_FILE) or DESKTOP_FOLDER)
        except Exception:
            pass

        # track open windows/dialogs as a list
        self.open_windows = []

        # icon items: key -> (widget, path, is_app, app_name)
        self.desktop_items = {}
        self._dragging = None

        # main overlay
        self.overlay = Gtk.Overlay()
        self.add(self.overlay)

        # wallpaper widget (from PART1)
        self.wallpaper = Wallpaper(DEFAULT_WALLPAPER)
        self.overlay.add(self.wallpaper)

        # modal backdrop (dim)
        self.modal_backdrop = Gtk.EventBox()
        self.modal_backdrop.set_name("modal-backdrop")
        self.modal_backdrop.set_above_child(True)
        self.overlay.add_overlay(self.modal_backdrop)
        self.modal_backdrop.set_valign(Gtk.Align.FILL)
        self.modal_backdrop.set_halign(Gtk.Align.FILL)
        self.modal_backdrop.connect("button-press-event", lambda *_: self._hide_overlay_and_popups())
        self.modal_backdrop.hide()

        # mini status (clock/battery/network)
        self._create_mini_status()

        # icon layer (absolute placement)
        self.icon_layer = Gtk.Fixed()
        self.icon_layer.set_name("icon-layer")
        self.overlay.add_overlay(self.icon_layer)

        # drag targets (use helper if available)
        try:
            targets = make_target_entries([("text/uri-list", 0, 0), ("text/plain", 0, 1)])
        except Exception:
            targets = [Gtk.TargetEntry.new("text/plain", 0, 0)]
        self.icon_layer.drag_dest_set(Gtk.DestDefaults.ALL, targets, Gdk.DragAction.COPY)
        self.icon_layer.connect("drag-data-received", self.on_drag_data_received)

        # bottom slim dock
        dock = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        dock.set_name("slim-dock")
        dock.set_halign(Gtk.Align.CENTER)
        dock.set_valign(Gtk.Align.END)
        dock.set_border_width(10)
        lp_btn = Gtk.Button()
        lp_btn.set_relief(Gtk.ReliefStyle.NONE)
        lp_btn.set_tooltip_text("Launchpad")
        lp_btn.set_image(load_icon("view-grid-symbolic", size=28))
        lp_btn.connect("clicked", lambda *_: self.toggle_launchpad())
        dock.pack_start(lp_btn, False, False, 0)

        for title, icon in APPS.items():
            btn = Gtk.Button()
            btn.set_relief(Gtk.ReliefStyle.NONE)
            img = load_icon(icon, size=36)
            btn.add(img)
            btn.set_tooltip_text(title)
            btn.connect("clicked", lambda _w, t=title: self.open_app(t))
            dock.pack_start(btn, False, False, 0)
        self.overlay.add_overlay(dock)

        # hot corner (top-right)
        hot = Gtk.EventBox()
        hot.set_size_request(48, 48)
        hot.set_name("hot-corner")
        hot.set_above_child(True)
        hot.set_halign(Gtk.Align.END)
        hot.set_valign(Gtk.Align.START)
        hot.connect("button-press-event", lambda *_: self.toggle_launchpad())
        self.overlay.add_overlay(hot)

        # Launchpad & Spotlight
        try:
            self.launchpad = Launchpad(self)
            self.spotlight = Spotlight(self)
        except Exception:
            self.launchpad = None
            self.spotlight = None

        # parallax smoothing
        self._parallax_target = (0.0, 0.0)
        GLib.timeout_add(16, self._parallax_step)

        # event masks and handlers
        self.add_events(
            Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.KEY_PRESS_MASK
        )
        self.connect("motion-notify-event", self._on_motion)
        self.connect("button-release-event", self._on_release)
        self.connect("key-press-event", self._on_key_press)

        # load state, style and show
        self.load_desktop_state()
        self.apply_css()

        try:
            if DEFAULT_WALLPAPER and os.path.exists(DEFAULT_WALLPAPER):
                lum = image_average_luminance(DEFAULT_WALLPAPER)
                self.apply_dark_icon_backdrop(lum > 0.6)
        except Exception:
            pass

        self.show_all()

    # ---------------- mini-status ----------------
    def _create_mini_status(self):
        status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        status_box.set_name("mini-status")

        clock_box = Gtk.EventBox()
        clock_box.set_name("mini-clock")
        self.clock = Gtk.Label()
        clock_box.add(self.clock)
        status_box.pack_start(clock_box, False, False, 0)

        self.batt_lbl = Gtk.Label(label="🔋--%")
        self.batt_lbl.set_name("mini-batt")
        status_box.pack_start(self.batt_lbl, False, False, 0)

        self.net_lbl = Gtk.Label(label="🌐")
        status_box.pack_start(self.net_lbl, False, False, 0)

        self.overlay.add_overlay(status_box)
        status_box.set_valign(Gtk.Align.START)
        status_box.set_halign(Gtk.Align.END)

        GLib.timeout_add_seconds(1, self._update_clock)
        GLib.timeout_add_seconds(30, self._update_status)

    def _update_clock(self):
        from datetime import datetime
        try:
            self.clock.set_text(datetime.now().strftime("%H:%M"))
        except Exception:
            pass
        return True

    def _update_status(self):
        try:
            self.batt_lbl.set_text("🔋 86%")
            self.net_lbl.set_text("🌐 Wi-Fi")
        except Exception:
            pass
        return True

    # ---------------- hide overlays ----------------
    def _hide_overlay_and_popups(self):
        try:
            if getattr(self, "launchpad", None) and getattr(self.launchpad, "get_visible", lambda: False)():
                try: self.launchpad.hide_lp()
                except Exception:
                    try: self.launchpad.hide()
                    except Exception: pass
            if getattr(self, "spotlight", None) and getattr(self.spotlight, "get_visible", lambda: False)():
                try: self.spotlight.hide_spotlight()
                except Exception:
                    try: self.spotlight.hide()
                    except Exception: pass
        except Exception:
            pass
        try:
            self.modal_backdrop.hide()
        except Exception:
            pass

    # ---------------- toggle launchpad ----------------
    def toggle_launchpad(self):
        lp = getattr(self, "launchpad", None)
        sp = getattr(self, "spotlight", None)
        if lp is None:
            return
        try:
            if sp is not None and sp.get_visible():
                try: sp.hide_spotlight()
                except Exception: sp.hide()
        except Exception:
            pass

        try:
            visible = lp.get_visible() if hasattr(lp, "get_visible") else False
            if visible:
                try: lp.hide_lp()
                except Exception:
                    try: lp.hide()
                    except Exception: pass
                try: self.modal_backdrop.hide()
                except Exception: pass
            else:
                try: self.modal_backdrop.show_all()
                except Exception: pass
                try: lp.populate()
                except Exception: pass
                try: lp.show_lp()
                except Exception:
                    try: lp.show_all(); lp.present()
                    except Exception: pass
        except Exception:
            pass

    # ---------------- window/app creation ----------------
    def _make_window_for(self, widget, win_title=None, width=720, height=480):
        try:
            win_title = win_title or "App"
            if isinstance(widget, Gtk.Dialog):
                dlg = widget
                try:
                    dlg.set_transient_for(self)
                    dlg.set_modal(False)
                except Exception:
                    pass
                dlg.show_all()
                self.open_windows.append(dlg)
                dlg.connect("destroy", lambda d: self._safe_remove_open_window(d))
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
            try:
                w.add(widget)
            except Exception:
                container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
                try:
                    container.pack_start(widget, True, True, 0)
                except Exception:
                    container.pack_start(Gtk.Label(label="Could not embed widget"), True, True, 0)
                w.add(container)

            w.connect("destroy", lambda win: self._safe_remove_open_window(win))
            w.show_all()
            w.present()
            self.open_windows.append(w)
            return w
        except Exception as e:
            try: info_dialog(self, "Xato", f"Oyna yaratishda xato:\n{e}")
            except Exception: pass
            return None

    def _safe_remove_open_window(self, win):
        try:
            if win in self.open_windows:
                self.open_windows.remove(win)
        except Exception:
            pass

    def open_app(self, title, payload=None):
        try:
            # Settings
            if title == "Settings":
                try:
                    dlg_obj = SettingsDialog(self)
                    if isinstance(dlg_obj, Gtk.Dialog):
                        try:
                            dlg_obj.set_transient_for(self)
                            dlg_obj.set_modal(False)
                        except Exception:
                            pass
                        dlg_obj.show_all()
                        self.open_windows.append(dlg_obj)
                        dlg_obj.connect("destroy", lambda d: self._safe_remove_open_window(d))
                        dlg_obj.present()
                        return
                    if hasattr(dlg_obj, "run") and callable(getattr(dlg_obj, "run")):
                        try:
                            dlg_obj.run()
                        except Exception as e:
                            info_dialog(self, "Settings", f"Settings ochishda xato:\n{e}")
                        return
                    try:
                        self._make_window_for(dlg_obj, win_title="Settings", width=640, height=480)
                        return
                    except Exception:
                        info_dialog(self, "Settings", "Settings oynasi ochilmadi")
                        return
                except Exception as e:
                    try:
                        SettingsDialog(self).run()
                        return
                    except Exception as e2:
                        info_dialog(self, "Xato", f"Settings ochishda xatolik:\n{e}\n{e2}")
                        return

            # Notepad
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
                self._make_window_for(nb, win_title=(os.path.basename(payload) if payload else "Notepad"), width=720, height=480)
                return

            # Calculator
            if title == "Calculator":
                calc = Calculator()
                self._make_window_for(calc, win_title="Calculator", width=420, height=220)
                return

            # Gallery
            if title == "Gallery":
                gal = Gallery()
                if payload and os.path.exists(payload):
                    try:
                        if hasattr(gal, "load"): gal.load(payload)
                        elif hasattr(gal, "open_path"): gal.open_path(payload)
                    except Exception:
                        pass
                self._make_window_for(gal, win_title="Gallery", width=900, height=600)
                return

            # FileManager
            if title == "FileManager":
                fm = FileManager()
                if payload and os.path.exists(payload):
                    try:
                        if hasattr(fm, "open_path"): fm.open_path(payload)
                        elif hasattr(fm, "set_path"): fm.set_path(payload)
                    except Exception:
                        pass
                self._make_window_for(fm, win_title="File Manager", width=900, height=600)
                return

            # MusicPlayer
            if title == "MusicPlayer":
                mp = MusicPlayer()
                self._make_window_for(mp, win_title="MusicPlayer", width=720, height=480)
                return

            # If payload: route by type
            if payload:
                if os.path.isdir(payload):
                    self.open_app("FileManager", payload=payload); return
                if os.path.exists(payload):
                    _, ext = os.path.splitext(payload.lower())
                    text_exts = (".txt", ".py", ".md", ".json", ".log", ".cfg", ".ini")
                    image_exts = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
                    video_exts = (".mp4", ".mov", ".mkv", ".webm", ".avi")
                    if ext in text_exts:
                        self.open_app("Notepad", payload=payload); return
                    if ext in image_exts or ext in video_exts:
                        self.open_app("Gallery", payload=payload); return
                    try:
                        if sys.platform == "darwin":
                            os.system(f'open "{payload}"')
                        elif sys.platform.startswith("win"):
                            os.startfile(payload)
                        else:
                            os.system(f'xdg-open "{payload}" &')
                        return
                    except Exception:
                        pass

            info_dialog(self, "Xato", f"{title} topilmadi yoki qo'llab-quvvatlanmaydi")
        except Exception as e:
            try: info_dialog(self, "Xato", f"open_app ichida xato:\n{e}")
            except Exception: pass

    # ---------------- icon helpers ----------------
    def _get_icon_image_from_box(self, box):
        try:
            child = box.get_child()
            if child and isinstance(child, Gtk.Box):
                kids = child.get_children()
                if kids:
                    maybe_img = kids[0]
                    if isinstance(maybe_img, Gtk.Image):
                        return maybe_img
        except Exception:
            pass
        return None

    def add_desktop_item(self, path, x=50, y=50, is_app=False, app_name=None):
        name = app_name if is_app else os.path.basename(path)
        box = Gtk.EventBox()
        box.set_name("desktop-icon")

        if is_app and app_name:
            icon_ref = APPS.get(app_name, "application-x-executable")
        else:
            if os.path.isdir(path):
                icon_ref = "folder"
            else:
                _, ext = os.path.splitext(path.lower())
                image_exts = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
                video_exts = (".mp4", ".mov", ".mkv", ".webm", ".avi")
                if ext in image_exts:
                    icon_ref = "image-x-generic"
                elif ext in video_exts:
                    icon_ref = "video-x-generic"
                else:
                    icon_ref = "text-x-generic"

        img = load_icon(icon_ref, size=ICON_SIZE)
        orig_pb = None
        try:
            if hasattr(img, "get_pixbuf"):
                orig_pb = img.get_pixbuf()
        except Exception:
            orig_pb = None

        # vbox: icon + label
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        vbox.set_halign(Gtk.Align.CENTER)
        vbox.set_valign(Gtk.Align.CENTER)
        vbox.pack_start(img, False, False, 0)

        lbl_text = name if len(name) <= 18 else name[:15] + "…"
        lbl = Gtk.Label(label=lbl_text)
        lbl.set_ellipsize(Pango.EllipsizeMode.END)
        lbl.set_xalign(0.5)
        lbl.get_style_context().add_class("desktop-label")
        vbox.pack_start(lbl, False, False, 0)

        box.add(vbox)
        box._label = lbl
        box._orig_pixbuf = orig_pb

        try:
            self.icon_layer.put(box, int(x), int(y))
        except Exception:
            try:
                self.icon_layer.put(box, 50, 50)
            except Exception:
                pass

        box.show_all()
        # use UUID for unique key
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
        box.connect("enter-notify-event", lambda wid, ev: self._icon_hover_enter(wid))
        box.connect("leave-notify-event", lambda wid, ev: self._icon_hover_leave(wid))

        self.save_desktop_state()
        return key

    def _icon_hover_enter(self, widget):
        try:
            orig_pb = getattr(widget, "_orig_pixbuf", None)
            img = self._get_icon_image_from_box(widget)
            if orig_pb and img:
                larger = orig_pb.scale_simple(max(1, int(ICON_SIZE * 1.25)), max(1, int(ICON_SIZE * 1.25)), GdkPixbuf.InterpType.BILINEAR)
                img.set_from_pixbuf(larger)
        except Exception:
            pass

    def _icon_hover_leave(self, widget):
        try:
            orig_pb = getattr(widget, "_orig_pixbuf", None)
            img = self._get_icon_image_from_box(widget)
            if orig_pb and img:
                img.set_from_pixbuf(orig_pb.scale_simple(ICON_SIZE, ICON_SIZE, GdkPixbuf.InterpType.BILINEAR))
        except Exception:
            pass

    # ---------------- dragging/moving ----------------
    def _on_icon_pressed(self, widget, event, key):
        if event.type == Gdk.EventType.BUTTON_PRESS and event.button == 1:
            try:
                x, y = self.icon_layer.child_get_position(widget)
            except Exception:
                x, y = 0, 0
            self._dragging = {"key": key, "wid": widget, "sx": int(event.x_root), "sy": int(event.y_root), "ox": int(x), "oy": int(y)}
            return True
        if event.button == 3:
            return self.on_desktop_item_click(widget, event, key)
        return False

    def _on_icon_released(self, widget, event, key):
        if self._dragging and self._dragging.get("key") == key:
            try:
                cur_x, cur_y = self.icon_layer.child_get_position(self._dragging["wid"])
                if SNAP_TO_GRID:
                    nx = int(round(cur_x / GRID_SIZE)) * GRID_SIZE
                    ny = int(round(cur_y / GRID_SIZE)) * GRID_SIZE
                    try:
                        self.icon_layer.move(self._dragging["wid"], nx, ny)
                    except Exception:
                        try:
                            self.icon_layer.put(self._dragging["wid"], nx, ny)
                        except Exception:
                            pass
            except Exception:
                pass
            self.save_desktop_state()
            self._dragging = None
            return True
        return False

    def _on_motion(self, win, ev):
        try:
            alloc = self.get_allocation()
            if alloc and alloc.width > 0 and alloc.height > 0:
                cx = alloc.width / 2.0
                cy = alloc.height / 2.0
                nx = (ev.x - cx) / max(1.0, cx)
                ny = (ev.y - cy) / max(1.0, cy)
                self._parallax_target = (nx * 0.6, ny * 0.6)
        except Exception:
            pass

        if not self._dragging:
            return False
        dx = int(ev.x_root - self._dragging["sx"])
        dy = int(ev.y_root - self._dragging["sy"])
        nx = max(0, self._dragging["ox"] + dx)
        ny = max(0, self._dragging["oy"] + dy)
        try:
            self.icon_layer.move(self._dragging["wid"], nx, ny)
        except Exception:
            pass
        return True

    def _parallax_step(self):
        try:
            curx, cury = getattr(self.wallpaper, "_parallax_offset", (0.0, 0.0))
            tx, ty = self._parallax_target
            nx = curx + (tx - curx) * 0.22
            ny = cury + (ty - cury) * 0.22
            try:
                if hasattr(self.wallpaper, "set_parallax_offset"):
                    self.wallpaper.set_parallax_offset(nx, ny)
                self.wallpaper._parallax_offset = (nx, ny)
            except Exception:
                self.wallpaper._parallax_offset = (nx, ny)
        except Exception:
            pass
        return True

    def _on_release(self, widget, event):
        if not getattr(self, "_dragging", None):
            return False
        try:
            wid = self._dragging.get("wid")
            cur_x, cur_y = self.icon_layer.child_get_position(wid)
            if SNAP_TO_GRID:
                nx = int(round(cur_x / GRID_SIZE)) * GRID_SIZE
                ny = int(round(cur_y / GRID_SIZE)) * GRID_SIZE
                try:
                    self.icon_layer.move(wid, nx, ny)
                except Exception:
                    try:
                        self.icon_layer.put(wid, nx, ny)
                    except Exception:
                        pass
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

        # double-click left
        if event.type == Gdk.EventType._2BUTTON_PRESS and event.button == 1:
            if is_app and app_name:
                self.open_app(app_name)
            else:
                self._open_entry(entry)
            return True

        # right-click context menu
        if event.type == Gdk.EventType.BUTTON_PRESS and event.button == 3:
            menu = Gtk.Menu()

            open_item = Gtk.MenuItem(label="Open")
            open_item.connect("activate", lambda *_: self._open_entry(entry))
            menu.append(open_item)

            open_new = Gtk.MenuItem(label="Open in new window")
            open_new.connect("activate", lambda *_: self._open_entry_new_window(entry))
            menu.append(open_new)

            # context-aware actions
            if not is_app and os.path.exists(target):
                _, ext = os.path.splitext(target.lower())
                text_exts = (".txt", ".py", ".md", ".json", ".log", ".cfg", ".ini")
                image_exts = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
                video_exts = (".mp4", ".mov", ".mkv", ".webm", ".avi")
                if ext in text_exts:
                    itm = Gtk.MenuItem(label="Open in Notepad")
                    itm.connect("activate", lambda *_: self.open_app("Notepad", payload=target))
                    menu.append(itm)
                if ext in image_exts or ext in video_exts:
                    itm = Gtk.MenuItem(label="Open in Gallery")
                    itm.connect("activate", lambda *_: self.open_app("Gallery", payload=target))
                    menu.append(itm)

            copy_path = Gtk.MenuItem(label="Copy path")
            def _copy_path(_):
                try:
                    cb = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
                    cb.set_text(target if target else "", -1)
                except Exception:
                    pass
            copy_path.connect("activate", _copy_path)
            menu.append(copy_path)

            reveal = Gtk.MenuItem(label="Reveal in File Manager")
            def _reveal(_):
                try:
                    folder = target if os.path.isdir(target) else os.path.dirname(target)
                    if sys.platform == "darwin":
                        os.system(f'open "{folder}"')
                    elif sys.platform.startswith("win"):
                        os.startfile(folder)
                    else:
                        os.system(f'xdg-open "{folder}" &')
                except Exception:
                    info_dialog(self, "Xato", f"Papkani ochib bo'lmadi: {folder}")
            reveal.connect("activate", _reveal)
            menu.append(reveal)

            rename_item = Gtk.MenuItem(label="Rename")
            rename_item.connect("activate", lambda *_: self._rename_entry(key))
            menu.append(rename_item)

            set_wp = Gtk.MenuItem(label="Set as wallpaper")
            set_wp.connect("activate", lambda *_: (self._set_wallpaper_from_path(target), self.save_desktop_state()))
            menu.append(set_wp)

            props = Gtk.MenuItem(label="Properties")
            props.connect("activate", lambda *_: self._show_properties(entry))
            menu.append(props)

            remove_item = Gtk.MenuItem(label="Remove")
            remove_item.connect("activate", lambda *_: self.remove_desktop_item(key))
            menu.append(remove_item)

            menu.show_all()
            try:
                menu.popup_at_pointer(event)
            except Exception:
                menu.popup(None, None, None, None, event.button, event.time)
            return True

        return False

    def _open_entry(self, entry):
        box, target, is_app, app_name = entry
        if is_app and app_name:
            self.open_app(app_name); return
        if os.path.isdir(target):
            self.open_app("FileManager", payload=target); return
        if os.path.exists(target):
            _, ext = os.path.splitext(target.lower())
            text_exts = (".txt", ".py", ".md", ".json", ".log", ".cfg", ".ini")
            image_exts = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
            video_exts = (".mp4", ".mov", ".mkv", ".webm", ".avi")
            if ext in text_exts:
                self.open_app("Notepad", payload=target); return
            if ext in image_exts or ext in video_exts:
                self.open_app("Gallery", payload=target); return
            try:
                if sys.platform == "darwin":
                    os.system(f'open "{target}"')
                elif sys.platform.startswith("win"):
                    os.startfile(target)
                else:
                    os.system(f'xdg-open "{target}" &')
                return
            except Exception:
                info_dialog(self, "Xato", f"Faylni ochishda xato: {target}")
                return
        info_dialog(self, "Xato", "Fayl topilmadi")

    def _open_entry_new_window(self, entry):
        # behave same as _open_entry for now
        self._open_entry(entry)

    def _rename_entry(self, key):
        entry = self.desktop_items.get(key)
        if not entry:
            return
        old_name = os.path.basename(entry[1])
        dlg = Gtk.Dialog("Rename", transient_for=self, flags=0)
        dlg.add_button(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL)
        dlg.add_button(Gtk.STOCK_OK, Gtk.ResponseType.OK)
        box = dlg.get_content_area()
        e = Gtk.Entry()
        e.set_text(old_name)
        box.add(e)
        dlg.show_all()
        if dlg.run() == Gtk.ResponseType.OK:
            newname = e.get_text().strip()
            if newname:
                try:
                    newpath = os.path.join(os.path.dirname(entry[1]), newname)
                    os.rename(entry[1], newpath)
                    self.desktop_items[key] = (entry[0], newpath, entry[2], entry[3])
                    try:
                        entry[0]._label.set_text(newname)
                    except Exception:
                        pass
                    self.save_desktop_state()
                except Exception as ex:
                    info_dialog(self, "Xato", f"Rename bo'lmadi:\n{ex}")
        dlg.destroy()

    def _show_properties(self, entry):
        box, target, is_app, app_name = entry
        try:
            st = os.stat(target) if os.path.exists(target) else None
            dlg = Gtk.MessageDialog(transient_for=self, flags=0, message_type=Gtk.MessageType.INFO, buttons=Gtk.ButtonsType.OK, text=os.path.basename(target))
            body = [f"Path: {target}"]
            if st:
                body.append(f"Size: {st.st_size} bytes")
                body.append(f"Mode: {oct(st.st_mode)}")
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
            if hasattr(self.wallpaper, "set_wallpaper"):
                self.wallpaper.set_wallpaper(path)
            self.save_desktop_state()
        except Exception as e:
            info_dialog(self, "Xato", f"Wallpaper o'rnatib bo'lmadi:\n{e}")

    # ---------------- drag & drop ----------------
    def on_drag_data_received(self, widget, drag_context, x, y, selection_data, info, time):
        try:
            uris = None
            try:
                uris = selection_data.get_uris()
            except Exception:
                uris = None
            if uris and len(uris) > 0:
                # use GLib.filename_from_uri for robust decoding
                u = uris[0]
                try:
                    path = GLib.filename_from_uri(u)[0]
                except Exception:
                    path = u
                    if u.startswith("file://"):
                        path = u[7:]
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
                        info_dialog(self, "Yaratildi", f"Desktop-ga qo‘yildi: {dest}")
                        drag_context.finish(True, False, time)
                        return
                    except Exception as e:
                        info_dialog(self, "Xato", f"Desktop-ga qo‘yib bo‘lmadi:\n{e}")
                        drag_context.finish(False, False, time)
                        return
            # fallback: plain text
            try:
                text = selection_data.get_text()
            except Exception:
                text = None
            if text:
                text = text.strip()
                if text in APPS:
                    self.add_desktop_item(text, x, y, is_app=True, app_name=text)
                    drag_context.finish(True, False, time); return
                if os.path.exists(text):
                    self.add_desktop_item(text, x, y, is_app=False)
                    drag_context.finish(True, False, time); return
        except Exception:
            pass
        drag_context.finish(False, False, time)

    # ---------------- persistence ----------------
    def save_desktop_state(self):
        try:
            ensure_desktop_folder(os.path.dirname(STATE_FILE) or DESKTOP_FOLDER)
            items = []
            for k, v in self.desktop_items.items():
                widget = v[0]
                try:
                    x, y = self.icon_layer.child_get_position(widget)
                except Exception:
                    x, y = 50, 50
                items.append({"path": v[1], "is_app": bool(v[2]), "app_name": v[3], "x": int(x), "y": int(y)})
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
                    self.add_desktop_item(it.get("path"), it.get("x", 50), it.get("y", 50), is_app=it.get("is_app", False), app_name=it.get("app_name"))
        except Exception:
            pass

    # ---------------- styling & keyboard ----------------
    def apply_css(self):
        css = """
        /* Minimal monochrome styling (GTK3-safe) */
        #mini-clock { color: rgba(255,255,255,0.95); font-weight:600; padding:6px; margin:8px; background: rgba(0,0,0,0.18); border-radius:8px; }
        #slim-dock { background: transparent; padding:8px; }
        #desktop-icon { border: 0; padding: 6px; margin: 8px; background: rgba(0,0,0,0.14); border-radius: 10px; }
        #desktop-icon image { margin:0; padding:0; }
        #desktop-icon:hover { background: rgba(0,0,0,0.22); }
        #desktop-icon .desktop-label { color: rgba(255,255,255,0.9); font-size: 11px; margin-top: 2px; }
        .lp-tile { border-radius: 12px; padding: 10px; min-width: 120px; min-height: 120px; margin: 6px; background: rgba(255,255,255,0.02); }
        .lp-tile:hover { margin-top: -6px; }
        /* modal backdrop to dim the desktop when Launchpad/Spotlight open */
        #modal-backdrop { background: rgba(8,8,8,0.52); }
        #hot-corner { background: transparent; }
        #mini-status { margin: 8px; }
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
                css = "#desktop-icon { background: rgba(0,0,0,0.18); padding:6px; border-radius:10px; }"
            else:
                css = "#desktop-icon { background: transparent; padding: 0; }"
            prov = Gtk.CssProvider()
            prov.load_from_data(css.encode("utf-8"))
            screen = Gdk.Screen.get_default()
            Gtk.StyleContext.add_provider_for_screen(screen, prov, Gtk.STYLE_PROVIDER_PRIORITY_USER)
        except Exception:
            pass

    def _on_key_press(self, widget, ev):
        name = (Gdk.keyval_name(ev.keyval) or "").lower()
        state = ev.state
        try:
            super_mask = (Gdk.ModifierType.SUPER_MASK | Gdk.ModifierType.MOD4_MASK | Gdk.ModifierType.META_MASK)
            is_super = bool(state & super_mask)
            if name == "g" and is_super:
                global SNAP_TO_GRID
                SNAP_TO_GRID = not SNAP_TO_GRID
                info_dialog(self, "Grid", f"Snap-to-grid: {SNAP_TO_GRID}")
                return True
            if name == "space" and is_super:
                if getattr(self, "spotlight", None) and getattr(self.spotlight, "get_visible", lambda: False)():
                    try: self.spotlight.hide_spotlight()
                    except Exception: self.spotlight.hide()
                else:
                    if getattr(self, "spotlight", None): self.spotlight.show_spotlight()
                return True
            if name == "l" and is_super:
                self.toggle_launchpad(); return True
            if name == "escape":
                if getattr(self, "launchpad", None) and getattr(self.launchpad, "get_visible", lambda: False)():
                    try: self.launchpad.hide(); self.modal_backdrop.hide()
                    except Exception: pass
                    return True
                if getattr(self, "spotlight", None) and getattr(self.spotlight, "get_visible", lambda: False)():
                    try: self.spotlight.hide(); self.modal_backdrop.hide()
                    except Exception: pass
                    return True
        except Exception:
            pass
        return False

# export
__all__ = ["Desktop", "DESKTOP_FOLDER"]
