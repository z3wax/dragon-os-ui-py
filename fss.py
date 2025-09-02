import gi
import os
import json
import stat
import sys
import shutil
import difflib
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib, Pango
from helpers import make_target_entries, info_dialog, load_icon, ensure_desktop_folder
from apps import Notepad, Calculator, Gallery, FileManager, MusicPlayer, SettingsDialog
APP_TITLE = "DragonOS"
DESKTOP_FOLDER = os.path.expanduser("~/Desktop")
STATE_FILE = os.path.join(DESKTOP_FOLDER, ".desktop_state.json")
DEFAULT_WALLPAPER = os.path.abspath("wallpaper.jpg") if os.path.exists("wallpaper.jpg") else None
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
        for yy in range(h):
            row_off = yy * stride
            for xx in range(w):
                off = row_off + xx * ch
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


# ---------- Wallpaper (unchanged but kept here) ----------
class Wallpaper(Gtk.EventBox):
    def __init__(self, path=None):
        super().__init__()
        self.image = Gtk.Image()
        self.add(self.image)
        self._pb = None
        self._path = None
        self._parallax_enabled = True
        self._parallax_offset = (0.0, 0.0)
        if path:
            self.set_wallpaper(path)

    def set_parallax(self, enabled=True):
        self._parallax_enabled = bool(enabled)

    def set_parallax_offset(self, nx, ny):
        self._parallax_offset = (max(-1.0, min(1.0, float(nx))), max(-1.0, min(1.0, float(ny))))
        alloc = self.get_allocation()
        if alloc and alloc.width > 0 and alloc.height > 0:
            self._update(alloc)

    def set_wallpaper(self, path):
        self._path = path if path and os.path.exists(path) else None
        if self._path:
            try:
                self._pb = GdkPixbuf.Pixbuf.new_from_file(self._path)
                alloc = self.get_allocation()
                if alloc and alloc.width > 0 and alloc.height > 0:
                    self._update(alloc)
                else:
                    GLib.idle_add(lambda: self._update(self.get_allocation()))
            except Exception:
                self._pb = None
                self.image.set_from_icon_name("image-missing", Gtk.IconSize.DIALOG)
        else:
            self._pb = None
            self.image.set_from_icon_name("applications-graphics", Gtk.IconSize.DIALOG)

    def do_size_allocate(self, allocation):
        Gtk.EventBox.do_size_allocate(self, allocation)
        if self._pb:
            self._update(allocation)

    def _update(self, allocation):
        try:
            if not self._pb or allocation.width <= 0 or allocation.height <= 0:
                return
            pb = self._pb
            w, h = allocation.width, allocation.height
            scale = max(w / max(1, pb.get_width()), h / max(1, pb.get_height()))
            nw, nh = int(pb.get_width() * scale), int(pb.get_height() * scale)
            scaled = pb.scale_simple(max(1, nw), max(1, nh), GdkPixbuf.InterpType.BILINEAR)

            base_x = max(0, (nw - w) // 2)
            base_y = max(0, (nh - h) // 2)

            if self._parallax_enabled:
                px, py = self._parallax_offset
                max_x = max(1, int(nw * 0.06))
                max_y = max(1, int(nh * 0.06))
                ox = base_x + int(px * max_x)
                oy = base_y + int(py * max_y)
                ox = max(0, min(nw - w, ox))
                oy = max(0, min(nh - h, oy))
            else:
                ox, oy = base_x, base_y

            cropped = scaled.new_subpixbuf(ox, oy, min(w, nw), min(h, nh))
            self.image.set_from_pixbuf(cropped)
        except Exception:
            pass


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
        ft = (filter_text or "").strip().lower()
        titles = list(APPS.keys())
        if ft:
            matches = fuzzy_matches(ft, titles)
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
        q = (text or "").strip().lower()
        for w in list(self.listbox.get_children()):
            self.listbox.remove(w)
        self.results = []
        if not q:
            return
        for title in APPS.keys():
            if q in title.lower() or q in title.lower():
                self._append_result({"type": "app", "title": title, "payload": title})
        try:
            if os.path.isdir(DESKTOP_FOLDER):
                for f in sorted(os.listdir(DESKTOP_FOLDER), key=lambda x: x.lower()):
                    if q in f.lower():
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
        ensure_desktop_folder(DESKTOP_FOLDER)

        self.add_events(
            Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.KEY_PRESS_MASK
        )

        self.overlay = Gtk.Overlay()
        self.add(self.overlay)

        self.wallpaper = Wallpaper(DEFAULT_WALLPAPER)
        self.overlay.add(self.wallpaper)

        # --- modal backdrop (dim translucent) ---
        self.modal_backdrop = Gtk.EventBox()
        self.modal_backdrop.set_name("modal-backdrop")
        self.modal_backdrop.set_above_child(True)
        # cover full area
        self.overlay.add_overlay(self.modal_backdrop)
        self.modal_backdrop.set_valign(Gtk.Align.FILL)
        self.modal_backdrop.set_halign(Gtk.Align.FILL)
        # intercept clicks: clicking backdrop will close Launchpad/Spotlight
        self.modal_backdrop.connect("button-press-event", lambda *_: self._hide_overlay_and_popups())
        self.modal_backdrop.hide()

        # mini status top-right (clock + battery + network)
        self._create_mini_status()

        # icon layer
        self.icon_layer = Gtk.Fixed()
        self.overlay.add_overlay(self.icon_layer)
        self.icon_layer.drag_dest_set(
            Gtk.DestDefaults.ALL, make_target_entries([("text/plain", 0, 0)]), Gdk.DragAction.COPY
        )
        self.icon_layer.connect("drag-data-received", self.on_drag_data_received)

        # slim bottom dock
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

        # hot-corner (top-right) to open Launchpad
        hot = Gtk.EventBox()
        hot.set_size_request(48, 48)
        hot.set_name("hot-corner")
        hot.set_above_child(True)
        hot.set_halign(Gtk.Align.END)
        hot.set_valign(Gtk.Align.START)
        hot.connect("button-press-event", lambda *_: self.toggle_launchpad())
        self.overlay.add_overlay(hot)

        self.desktop_items = {}
        self._dragging = None

        self.launchpad = Launchpad(self)
        self.spotlight = Spotlight(self)

        # parallax smoothing state
        self._parallax_target = (0.0, 0.0)
        GLib.timeout_add(16, self._parallax_step)

        self.connect("motion-notify-event", self._on_motion)
        self.connect("button-release-event", self._on_release)
        self.connect("key-press-event", self._on_key_press)

        self.load_desktop_state()
        self.apply_css()
        if DEFAULT_WALLPAPER and os.path.exists(DEFAULT_WALLPAPER):
            lum = image_average_luminance(DEFAULT_WALLPAPER)
            self.apply_dark_icon_backdrop(lum > 0.6)

        self.show_all()

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

        # NOTE: do NOT call status_box.set_above_child(True) — Box doesn't have that method.
        # overlay.add_overlay already puts it above the main child.
        self.overlay.add_overlay(status_box)
        status_box.set_valign(Gtk.Align.START)
        status_box.set_halign(Gtk.Align.END)

        # update every 30s
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
            # Minimal placeholder: integrate with upower / nmcli for real values
            self.batt_lbl.set_text("🔋 86%")
            self.net_lbl.set_text("🌐 Wi-Fi")
        except Exception:
            pass
        return True

    def _hide_overlay_and_popups(self):
        try:
            if getattr(self, "launchpad", None) and self.launchpad.get_visible():
                try:
                    self.launchpad.hide_lp()
                except Exception:
                    self.launchpad.hide()
            if getattr(self, "spotlight", None) and self.spotlight.get_visible():
                try:
                    self.spotlight.hide_spotlight()
                except Exception:
                    self.spotlight.hide()
        except Exception:
            pass
        try:
            self.modal_backdrop.hide()
        except Exception:
            pass

    def toggle_launchpad(self):
        lp = getattr(self, "launchpad", None)
        sp = getattr(self, "spotlight", None)
        if lp is None:
            return
        # if spotlight open, close it first
        try:
            if sp is not None and sp.get_visible():
                try:
                    sp.hide_spotlight()
                except Exception:
                    sp.hide()
        except Exception:
            pass

        try:
            visible = False
            try:
                visible = lp.get_visible()
            except Exception:
                visible = False
            if visible:
                try:
                    lp.hide_lp()
                except Exception:
                    lp.hide()
                try:
                    self.modal_backdrop.hide()
                except Exception:
                    pass
            else:
                # show dim backdrop first (so desktop looks blurred/dim)
                try:
                    self.modal_backdrop.show_all()
                except Exception:
                    pass
                # then open launchpad
                try:
                    lp.populate()
                except Exception:
                    pass
                try:
                    lp.show_lp()
                except Exception:
                    lp.show_all(); lp.present()
        except Exception:
            pass

    def open_app(self, title, payload=None):
        """
        Open an app in a non-blocking top-level window so multiple apps can run in parallel.
        - title: one of known app names ("Notepad", "Calculator", "Gallery", "FileManager", "MusicPlayer", "Settings") or a custom.
        - payload: optional path (file/folder) or other data passed to the app.
        The function tries to open widgets non-modally; if an app only supports modal Dialog.run(),
        it falls back to running it modally as a last resort.
        """

        # helper: create a top-level window and put `widget` inside it (non-modal)
        def _make_window_for(widget, win_title=None, width=720, height=480):
            try:
                win_title = win_title or title
                w = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
                w.set_transient_for(self)  # link but not modal
                w.set_title(win_title)
                w.set_default_size(width, height)
                # allow keyboard close / decorations as normal
                try:
                    # If a widget is a Gtk.Dialog, show it instead (dialogs are top-level themselves)
                    if isinstance(widget, Gtk.Dialog):
                        dlg = widget
                        # try to make it non-modal and show it
                        try:
                            dlg.set_transient_for(self)
                            dlg.set_modal(False)
                        except Exception:
                            pass
                        dlg.show_all()
                        # track the dialog as an open window (destroy handler cleans up)
                        self.open_windows.append(dlg)
                        dlg.connect("destroy",
                                    lambda d: self.open_windows.remove(d) if d in self.open_windows else None)
                        dlg.present()
                        return dlg
                    # otherwise, widget is a Gtk.Widget -> pack into window
                    # remove parent if any (defensive)
                    try:
                        parent = widget.get_parent()
                        if parent:
                            parent.remove(widget)
                    except Exception:
                        pass
                    w.add(widget)
                except Exception:
                    # fallback: wrap widget inside a Box if adding directly fails
                    container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
                    try:
                        container.pack_start(widget, True, True, 0)
                    except Exception:
                        # if packing fails, show a label with error
                        container.pack_start(Gtk.Label(label="Could not embed widget"), True, True, 0)
                    w.add(container)

                # cleanup list on destroy
                def _on_destroy(win):
                    try:
                        if win in self.open_windows:
                            self.open_windows.remove(win)
                    except Exception:
                        pass

                w.connect("destroy", _on_destroy)
                w.show_all()
                w.present()
                self.open_windows.append(w)
                return w
            except Exception as e:
                info_dialog(self, "Xato", f"Oyna yaratishda xato:\n{e}")
                return None

        # try-catch top-level to avoid bubbling exceptions
        try:
            # --- Settings ---
            if title == "Settings":
                try:
                    dlg = SettingsDialog(self)
                    # If SettingsDialog returns a Dialog, prefer non-blocking show_all
                    if isinstance(dlg, Gtk.Dialog):
                        try:
                            dlg.set_transient_for(self)
                            dlg.set_modal(False)
                        except Exception:
                            pass
                        dlg.show_all()
                        self.open_windows.append(dlg)
                        dlg.connect("destroy",
                                    lambda d: self.open_windows.remove(d) if d in self.open_windows else None)
                        dlg.present()
                        return
                    else:
                        # widget-like settings
                        _make_window_for(dlg, win_title="Settings", width=640, height=480)
                        return
                except Exception:
                    # fallback: try modal run()
                    try:
                        SettingsDialog(self).run()
                        return
                    except Exception as e:
                        info_dialog(self, "Xato", f"Settings ochishda xato:\n{e}")
                        return

            # --- Notepad ---
            if title == "Notepad":
                try:
                    nb = Notepad()
                    # if payload is a path, try to load content into the notepad buffer
                    if payload and os.path.exists(payload):
                        try:
                            with open(payload, "r", encoding="utf-8") as f:
                                data = f.read(200000)
                            try:
                                buf = nb.view.get_buffer()
                                buf.set_text(data)
                            except Exception:
                                # maybe Notepad exposes a load() method
                                try:
                                    if hasattr(nb, "load"):
                                        nb.load(payload)
                                except Exception:
                                    pass
                        except Exception:
                            pass
                    _make_window_for(nb, win_title=(os.path.basename(payload) if payload else "Notepad"), width=720,
                                     height=480)
                    return
                except Exception as e:
                    info_dialog(self, "Xato", f"Notepad ochishda xato:\n{e}")
                    return

            # --- Calculator ---
            if title == "Calculator":
                try:
                    calc = Calculator()
                    _make_window_for(calc, win_title="Calculator", width=420, height=220)
                    return
                except Exception as e:
                    info_dialog(self, "Xato", f"Calculator ochishda xato:\n{e}")
                    return

            # --- Gallery ---
            if title == "Gallery":
                try:
                    gal = Gallery()
                    # if payload is an image, and Gallery supports opening a path, try to call load()
                    if payload and os.path.exists(payload):
                        try:
                            if hasattr(gal, "load"):
                                gal.load(payload)
                            elif hasattr(gal, "open_path"):
                                gal.open_path(payload)
                        except Exception:
                            pass
                    _make_window_for(gal, win_title="Gallery", width=900, height=600)
                    return
                except Exception as e:
                    info_dialog(self, "Xato", f"Gallery ochishda xato:\n{e}")
                    return

            # --- FileManager ---
            if title == "FileManager":
                try:
                    fm = FileManager()
                    # if payload is a folder path, try to instruct fm to open it
                    if payload and os.path.exists(payload):
                        try:
                            if hasattr(fm, "open_path"):
                                fm.open_path(payload)
                            elif hasattr(fm, "set_path"):
                                fm.set_path(payload)
                        except Exception:
                            pass
                    _make_window_for(fm, win_title="File Manager", width=900, height=600)
                    return
                except Exception as e:
                    info_dialog(self, "Xato", f"FileManager ochishda xato:\n{e}")
                    return

            # --- MusicPlayer ---
            if title == "MusicPlayer":
                try:
                    mp = MusicPlayer()
                    _make_window_for(mp, win_title="MusicPlayer", width=720, height=480)
                    return
                except Exception as e:
                    info_dialog(self, "Xato", f"MusicPlayer ochishda xato:\n{e}")
                    return

            # --- Generic: try to open a file with Notepad/Gallery or system opener ---
            if payload:
                # if payload exists, handle as file/folder
                if os.path.isdir(payload):
                    # open in FileManager
                    self.open_app("FileManager", payload=payload)
                    return
                if os.path.exists(payload):
                    _, ext = os.path.splitext(payload.lower())
                    text_exts = (".txt", ".py", ".md", ".json", ".log", ".cfg", ".ini")
                    image_exts = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
                    if ext in text_exts:
                        self.open_app("Notepad", payload=payload)
                        return
                    if ext in image_exts:
                        self.open_app("Gallery", payload=payload)
                        return
                    # fallback: try system default opener
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

            # If reach here: unknown app
            info_dialog(self, "Xato", f"{title} topilmadi yoki qo'llab-quvvatlanmaydi")
            return

        except Exception as e:
            # catch-all to avoid crashing desktop
            try:
                info_dialog(self, "Xato", f"open_app ichida xato:\n{e}")
            except Exception:
                pass
            return

    def choose_wallpaper_from_dialog(self, parent_dialog):
        dlg = Gtk.FileChooserDialog(
            title="Wallpaper tanlang",
            parent=parent_dialog,
            action=Gtk.FileChooserAction.OPEN,
        )
        dlg.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OPEN, Gtk.ResponseType.OK)
        flt = Gtk.FileFilter()
        flt.set_name("Images")
        for pat in ["*.png", "*.jpg", "*.jpeg", "*.webp", "*.bmp"]:
            flt.add_pattern(pat)
        dlg.add_filter(flt)
        if dlg.run() == Gtk.ResponseType.OK:
            try:
                self.wallpaper.set_wallpaper(dlg.get_filename())
                lum = image_average_luminance(dlg.get_filename())
                self.apply_dark_icon_backdrop(lum > 0.6)
            except Exception as e:
                info_dialog(self, "Xato", f"Wallpaperni o'rnatib bo'lmadi:\n{e}")
        dlg.destroy()

    def toggle_dark(self, enabled):
        try:
            settings = Gtk.Settings.get_default()
            settings.set_property("gtk-application-prefer-dark-theme", bool(enabled))
        except Exception:
            pass

    def apply_dark_icon_backdrop(self, enable=True):
        if enable:
            css = "#desktop-icon { background: rgba(0,0,0,0.18); padding:6px; border-radius:10px; }"
        else:
            css = "#desktop-icon { background: transparent; padding: 0; }"
        prov = Gtk.CssProvider()
        prov.load_from_data(css.encode("utf-8"))
        screen = Gdk.Screen.get_default()
        Gtk.StyleContext.add_provider_for_screen(screen, prov, Gtk.STYLE_PROVIDER_PRIORITY_USER)

    # Icon management (enhanced: image + label)
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
                icon_ref = "text-x-generic"

        img = load_icon(icon_ref, size=ICON_SIZE)
        orig_pb = None
        try:
            if hasattr(img, "get_pixbuf"):
                orig_pb = img.get_pixbuf()
        except Exception:
            orig_pb = None

        # create vbox with icon + label
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
        key = f"{name}-{len(self.desktop_items)}"
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
            child = widget.get_child() if hasattr(widget, "get_child") else None
            if orig_pb and child and isinstance(child, Gtk.Image):
                larger = orig_pb.scale_simple(max(1, int(ICON_SIZE * 1.25)), max(1, int(ICON_SIZE * 1.25)), GdkPixbuf.InterpType.BILINEAR)
                child.set_from_pixbuf(larger)
        except Exception:
            pass

    def _icon_hover_leave(self, widget):
        try:
            orig_pb = getattr(widget, "_orig_pixbuf", None)
            child = widget.get_child() if hasattr(widget, "get_child") else None
            if orig_pb and child and isinstance(child, Gtk.Image):
                child.set_from_pixbuf(orig_pb.scale_simple(ICON_SIZE, ICON_SIZE, GdkPixbuf.InterpType.BILINEAR))
        except Exception:
            pass

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
                # set smoothing target
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
            curx, cury = self.wallpaper._parallax_offset
            tx, ty = self._parallax_target
            nx = curx + (tx - curx) * 0.22
            ny = cury + (ty - cury) * 0.22
            self.wallpaper.set_parallax_offset(nx, ny)
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

    def on_desktop_item_click(self, widget, event, key):
        """
        Context menu for desktop items (right-click).
        Provides: Open, Open in new window, Open in Notepad/Gallery (context-aware),
        Copy path, Reveal in File Manager, Rename, Set as wallpaper, Properties, Remove.
        """
        entry = self.desktop_items.get(key)
        if not entry:
            return False
        box, target, is_app, app_name = entry

        # Double-click handler (left button)
        if event.type == Gdk.EventType._2BUTTON_PRESS and event.button == 1:
            if is_app and app_name:
                self.open_app(app_name)
            else:
                # default open behavior
                info_dialog(self, "Open", f"Ochish: {target}\n(Demo: tizim ochishni qo'shing)")
            return True

        # Right-click -> context menu
        if event.type == Gdk.EventType.BUTTON_PRESS and event.button == 3:
            menu = Gtk.Menu()

            # Open (default)
            open_item = Gtk.MenuItem(label="Open")
            open_item.connect("activate", lambda *_: self._open_entry(entry))
            menu.append(open_item)

            # Open in new window (non-blocking)
            open_new = Gtk.MenuItem(label="Open in new window")
            open_new.connect("activate", lambda *_: self._open_entry_new_window(entry))
            menu.append(open_new)

            # Context-aware actions for files (not apps)
            target_path = target
            if not is_app and os.path.exists(target_path):
                _, ext = os.path.splitext(target_path.lower())
                text_exts = (".txt", ".py", ".md", ".json", ".log", ".cfg", ".ini")
                image_exts = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")

                if ext in text_exts:
                    open_in_notepad = Gtk.MenuItem(label="Open in Notepad")
                    open_in_notepad.connect("activate", lambda *_: self.open_app("Notepad", payload=target_path))
                    menu.append(open_in_notepad)

                if ext in image_exts:
                    open_in_gallery = Gtk.MenuItem(label="Open in Gallery")
                    open_in_gallery.connect("activate", lambda *_: self.open_app("Gallery", payload=target_path))
                    menu.append(open_in_gallery)

            # Copy path to clipboard
            copy_path = Gtk.MenuItem(label="Copy path")

            def _copy_path(_):
                try:
                    cb = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
                    cb.set_text(target_path if target_path else "", -1)
                except Exception:
                    pass

            copy_path.connect("activate", _copy_path)
            menu.append(copy_path)

            # Reveal in file manager
            reveal = Gtk.MenuItem(label="Reveal in File Manager")

            def _reveal(_):
                try:
                    folder = target_path if os.path.isdir(target_path) else os.path.dirname(target_path)
                    if sys.platform == "darwin":
                        os.system(f'open "{folder}"')
                    elif sys.platform.startswith("win"):
                        os.startfile(folder)
                    else:
                        # use xdg-open in background
                        os.system(f'xdg-open "{folder}" &')
                except Exception:
                    info_dialog(self, "Xato", f"Papkani ochib bo'lmadi: {folder}")

            reveal.connect("activate", _reveal)
            menu.append(reveal)

            # Rename
            rename_item = Gtk.MenuItem(label="Rename")
            rename_item.connect("activate", lambda *_: self._rename_entry(key))
            menu.append(rename_item)

            # Set as wallpaper (only for files that exist)
            set_wp = Gtk.MenuItem(label="Set as wallpaper")
            set_wp.connect("activate",
                           lambda *_: (self.wallpaper.set_wallpaper(target_path), self.save_desktop_state()))
            menu.append(set_wp)

            # Properties
            props = Gtk.MenuItem(label="Properties")
            props.connect("activate", lambda *_: self._show_properties(entry))
            menu.append(props)

            # Remove
            remove_item = Gtk.MenuItem(label="Remove")
            remove_item.connect("activate", lambda *_: self.remove_desktop_item(key))
            menu.append(remove_item)

            menu.show_all()
            # show at pointer (GTK3)
            try:
                menu.popup_at_pointer(event)
            except Exception:
                # fallback older API if needed
                menu.popup(None, None, None, None, event.button, event.time)
            return True

        return False

    # --- yordamchi funksiyalar ---
    def _open_entry(self, entry):
        """Default Open: opens app or shows file (tries sensible handler)."""
        box, target, is_app, app_name = entry
        if is_app and app_name:
            # Open the app (non-blocking)
            self.open_app(app_name)
            return

        # If folder -> open FileManager
        if os.path.isdir(target):
            self.open_app("FileManager", payload=target)
            return

        # If file -> choose handler by extension or system default
        if os.path.exists(target):
            _, ext = os.path.splitext(target.lower())
            text_exts = (".txt", ".py", ".md", ".json", ".log", ".cfg", ".ini")
            image_exts = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
            if ext in text_exts:
                self.open_app("Notepad", payload=target)
                return
            if ext in image_exts:
                self.open_app("Gallery", payload=target)
                return
            # fallback: use system default opener
            try:
                if sys.platform == "darwin":
                    os.system(f'open "{target}"')
                elif sys.platform.startswith("win"):
                    os.startfile(target)
                else:
                    os.system(f'xdg-open "{target}" &')
            except Exception:
                info_dialog(self, "Xato", f"Faylni ochishda xato: {target}")
            return

        info_dialog(self, "Xato", "Fayl topilmadi")

    def _open_entry_new_window(self, entry):
        """Open the entry in a new non-blocking window (context-aware)."""
        box, target, is_app, app_name = entry
        if is_app and app_name:
            # open the app in a new top-level window
            self.open_app(app_name)
            return

        if os.path.isdir(target):
            self.open_app("FileManager", payload=target)
            return

        if os.path.exists(target):
            _, ext = os.path.splitext(target.lower())
            text_exts = (".txt", ".py", ".md", ".json", ".log", ".cfg", ".ini")
            image_exts = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
            if ext in text_exts:
                self.open_app("Notepad", payload=target)
                return
            if ext in image_exts:
                self.open_app("Gallery", payload=target)
                return
            # default to Notepad preview
            self.open_app("Notepad", payload=target)
            return

        info_dialog(self, "Xato", "Fayl topilmadi")

    def _open_entry(self, entry):
        box, target, is_app, app_name = entry
        if is_app and app_name:
            self.open_app(app_name)
        else:
            # open file in default behavior: if text -> Notepad dialog; else reveal
            if os.path.isdir(target):
                self.open_app("FileManager", payload=target)
            elif os.path.exists(target):
                _, ext = os.path.splitext(target.lower())
                text_exts = (".txt", ".py", ".md", ".json", ".log", ".cfg", ".ini")
                image_exts = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
                if ext in text_exts:
                    # open in the same Notepad pattern as new window
                    self.open_app("Notepad", payload=target)
                elif ext in image_exts:
                    self.open_app("Gallery", payload=target)
                else:
                    # attempt to open via system default
                    try:
                        if sys.platform == "darwin":
                            os.system(f'open "{target}"')
                        elif sys.platform.startswith("win"):
                            os.startfile(target)
                        else:
                            os.system(f'xdg-open "{target}" &')
                    except Exception:
                        info_dialog(self, "Xato", f"Faylni ochishda xato: {target}")
            else:
                info_dialog(self, "Xato", "Fayl topilmadi")

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
                newpath = os.path.join(os.path.dirname(entry[1]), newname)
                try:
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
            body = []
            body.append(f"Path: {target}")
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

    def _open_entry_new_window(self, entry):
        box, target, is_app, app_name = entry
        if is_app and app_name:
            # open the app in a new top-level window
            self.open_app(app_name)
            return
        # otherwise treat as a file
        if os.path.isdir(target):
            # open folder in FileManager window
            self.open_app("FileManager", payload=target)
        elif os.path.exists(target):
            # choose Notepad or Gallery depending on extension
            _, ext = os.path.splitext(target.lower())
            text_exts = (".txt", ".py", ".md", ".json", ".log", ".cfg", ".ini")
            image_exts = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
            if ext in text_exts:
                self.open_app("Notepad", payload=target)
            elif ext in image_exts:
                self.open_app("Gallery", payload=target)
            else:
                # default: try open with Notepad to preview
                self.open_app("Notepad", payload=target)
        else:
            info_dialog(self, "Xato", "Fayl topilmadi")

    def on_drag_data_received(self, widget, drag_context, x, y, selection_data, info, time):
        text = selection_data.get_text()
        if not text:
            drag_context.finish(False, False, time)
            return
        if text in APPS:
            self.add_desktop_item(text, x, y, is_app=True, app_name=text)
            drag_context.finish(True, False, time)
            return
        path = text
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
        drag_context.finish(False, False, time)

    # Persistence
    def save_desktop_state(self):
        try:
            ensure_desktop_folder(DESKTOP_FOLDER)
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
        prov = Gtk.CssProvider()
        prov.load_from_data(css.encode("utf-8"))
        screen = Gdk.Screen.get_default()
        Gtk.StyleContext.add_provider_for_screen(screen, prov, Gtk.STYLE_PROVIDER_PRIORITY_USER)

    # Keyboard: Super+G toggle grid, Super+Space show Spotlight, Super+L open Launchpad
    def _on_key_press(self, widget, ev):
        name = (Gdk.keyval_name(ev.keyval) or "").lower()
        state = ev.state
        try:
            super_mask = (Gdk.ModifierType.SUPER_MASK | Gdk.ModifierType.MOD4_MASK | Gdk.ModifierType.META_MASK)
            is_super = bool(state & super_mask)

            # Super+G toggles snap-to-grid
            if name == "g" and is_super:
                global SNAP_TO_GRID
                SNAP_TO_GRID = not SNAP_TO_GRID
                info_dialog(self, "Grid", f"Snap-to-grid: {SNAP_TO_GRID}")
                return True

            # Super+Space opens spotlight
            if name == "space" and is_super:
                if self.spotlight.get_visible():
                    self.spotlight.hide_spotlight()
                else:
                    self.spotlight.show_spotlight()
                return True

            # Super+L toggles launchpad
            if name == "l" and is_super:
                self.toggle_launchpad()
                return True

            # Esc closes Launchpad/Spotlight
            if name == "escape":
                if self.launchpad.get_visible():
                    self.launchpad.hide()
                    self.modal_backdrop.hide()
                    return True
                if self.spotlight.get_visible():
                    self.spotlight.hide()
                    self.modal_backdrop.hide()
                    return True

        except Exception:
            pass
        return False


# export
__all__ = ["Desktop", "DESKTOP_FOLDER"]
















# -*- coding: utf-8 -*-
import os
import sys
import json
import shutil
from gi.repository import Gtk, Gdk, GLib, GdkPixbuf, Pango

# ---------- Minimal helper utilities (moslashtiring) ----------
ICON_SIZE = 64
GRID_SIZE = 96
SNAP_TO_GRID = True
DEFAULT_WALLPAPER = None
STATE_FILE = os.path.expanduser("~/.local/share/mydesktop/desktop_state.json")
DESKTOP_FOLDER = os.path.expanduser("~/Desktop")
APPS = {
    "Notepad": "accessories-text-editor",
    "Calculator": "accessories-calculator",
    "Gallery": "image-x-generic",
    "FileManager": "system-file-manager",
    "MusicPlayer": "multimedia-player",
    "Settings": "preferences-system",
}

def ensure_desktop_folder(path):
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:
        pass

def load_icon(name_or_path, size=ICON_SIZE):
    # name_or_path could be icon name or path to file
    try:
        if os.path.exists(name_or_path):
            pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(name_or_path, size, size, True)
            img = Gtk.Image.new_from_pixbuf(pb)
            return img
        else:
            # Try icon name
            img = Gtk.Image.new_from_icon_name(name_or_path, Gtk.IconSize.BUTTON)
            # ensure pixbuf for scaling if possible
            try:
                pb = img.get_pixbuf()
                if pb:
                    pb = pb.scale_simple(size, size, GdkPixbuf.InterpType.BILINEAR)
                    img.set_from_pixbuf(pb)
            except Exception:
                pass
            return img
    except Exception:
        return Gtk.Image()

def image_average_luminance(path):
    # simple placeholder: return 0.5 neutral
    return 0.5

def info_dialog(parent, title, text):
    try:
        dlg = Gtk.MessageDialog(transient_for=parent, flags=0,
                                message_type=Gtk.MessageType.INFO,
                                buttons=Gtk.ButtonsType.OK, text=title)
        dlg.format_secondary_text(text)
        dlg.run()
        dlg.destroy()
    except Exception:
        pass
