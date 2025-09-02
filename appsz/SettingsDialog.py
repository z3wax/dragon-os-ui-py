# settings_dialog.py
# GTK3 Settings dialog for DragonOS (modern-ish, JSON-backed)
import gi
import os
import json
from pathlib import Path

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib

CONFIG_DIR = Path(os.path.expanduser("~/.config/dragonos"))
CONFIG_FILE = CONFIG_DIR / "settings.json"

DEFAULTS = {
    "language": "en_US",
    "theme": "auto",      # auto / light / dark
    "show_clock": True,
    "snap_to_grid": True,
    "wallpaper": "",
    "privacy": {
        "telemetry": False,
        "auto_updates": True
    }
}

class SettingsDialog(Gtk.Dialog):
    """
    Zamonaviy ko'rinishdagi (GTK3) sozlamalar oynasi.
    - JSON bilan ~/.config/dragonos/settings.json ga saqlaydi
    - Non-blocking save (GLib.idle_add)
    - Parent.desktop.wallpaper mavjud bo'lsa, wallpaper preview qo'llanadi
    """

    def __init__(self, parent=None, on_apply_callback=None):
        """
        :param parent: transient_for oynani ko'rsatish uchun asosiy oyna
        :param on_apply_callback: (optional) funksiyani chaqiradi apply bosilganda (masalan: parent.update_ui)
        """
        super().__init__(title="Settings", transient_for=parent, flags=0)
        self.set_default_size(720, 480)
        self.set_modal(True)
        self.set_resizable(False)
        self.on_apply_callback = on_apply_callback
        self.parent = parent

        # data
        self._prefs = {}
        self._widgets = {}

        # UI build
        self._build_ui()
        self._load_prefs()
        self._bind_widgets()
        self.show_all()

    # ---------------- UI ----------------
    def _build_ui(self):
        box = self.get_content_area()
        box.set_border_width(12)

        # Notebook tabs
        nb = Gtk.Notebook()
        nb.set_tab_pos(Gtk.PositionType.LEFT)
        box.pack_start(nb, True, True, 0)
        self.notebook = nb

        # --- General tab ---
        gen_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        gen_box.set_border_width(8)

        # Language
        h = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        lbl = Gtk.Label(label="Language:")
        lbl.set_xalign(0)
        h.pack_start(lbl, False, False, 0)
        lang = Gtk.ComboBoxText()
        for code, nice in [("en_US", "English"), ("uz_UZ", "O'zbek"), ("ru_RU", "Русский")]:
            lang.append_text(f"{code} — {nice}")
        lang.set_hexpand(True)
        h.pack_start(lang, True, True, 0)
        gen_box.pack_start(h, False, False, 0)
        self._widgets['language'] = lang

        # Snap-to-grid toggle
        snap_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        snap_lbl = Gtk.Label(label="Snap desktop icons to grid")
        snap_lbl.set_xalign(0)
        snap_switch = Gtk.Switch()
        snap_row.pack_start(snap_lbl, True, True, 0)
        snap_row.pack_start(snap_switch, False, False, 0)
        gen_box.pack_start(snap_row, False, False, 0)
        self._widgets['snap_to_grid'] = snap_switch

        # Show clock
        clk_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        clk_lbl = Gtk.Label(label="Show clock in status")
        clk_lbl.set_xalign(0)
        clk_switch = Gtk.Switch()
        clk_row.pack_start(clk_lbl, True, True, 0)
        clk_row.pack_start(clk_switch, False, False, 0)
        gen_box.pack_start(clk_row, False, False, 0)
        self._widgets['show_clock'] = clk_switch

        nb.append_page(gen_box, Gtk.Label(label="General"))

        # --- Appearance tab ---
        app_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        app_box.set_border_width(8)

        # Theme selection
        th_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        th_lbl = Gtk.Label(label="Theme:")
        th_lbl.set_xalign(0)
        th_combo = Gtk.ComboBoxText()
        th_combo.append_text("auto")
        th_combo.append_text("light")
        th_combo.append_text("dark")
        th_combo.set_active(0)
        th_row.pack_start(th_lbl, False, False, 0)
        th_row.pack_start(th_combo, True, True, 0)
        app_box.pack_start(th_row, False, False, 0)
        self._widgets['theme'] = th_combo

        # Wallpaper chooser + preview
        wp_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        wp_lbl = Gtk.Label(label="Wallpaper:")
        wp_lbl.set_xalign(0)
        wp_btn = Gtk.FileChooserButton(title="Select wallpaper", action=Gtk.FileChooserAction.OPEN)
        wp_btn.set_use_preview(True)
        wp_btn.set_hexpand(True)
        wp_btn.connect("file-set", self._on_wallpaper_selected)
        wp_preview_btn = Gtk.Button(label="Preview")
        wp_preview_btn.connect("clicked", self._on_preview_wallpaper)
        wp_row.pack_start(wp_lbl, False, False, 0)
        wp_row.pack_start(wp_btn, True, True, 0)
        wp_row.pack_start(wp_preview_btn, False, False, 0)
        app_box.pack_start(wp_row, False, False, 0)
        self._widgets['wallpaper'] = wp_btn

        nb.append_page(app_box, Gtk.Label(label="Appearance"))

        # --- Privacy tab ---
        priv_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        priv_box.set_border_width(8)

        telemetry_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        telemetry_lbl = Gtk.Label(label="Send anonymous telemetry")
        telemetry_lbl.set_xalign(0)
        telemetry_switch = Gtk.Switch()
        telemetry_row.pack_start(telemetry_lbl, True, True, 0)
        telemetry_row.pack_start(telemetry_switch, False, False, 0)
        priv_box.pack_start(telemetry_row, False, False, 0)
        self._widgets['telemetry'] = telemetry_switch

        updates_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        updates_lbl = Gtk.Label(label="Enable automatic updates")
        updates_lbl.set_xalign(0)
        updates_switch = Gtk.Switch()
        updates_row.pack_start(updates_lbl, True, True, 0)
        updates_row.pack_start(updates_switch, False, False, 0)
        priv_box.pack_start(updates_row, False, False, 0)
        self._widgets['auto_updates'] = updates_switch

        nb.append_page(priv_box, Gtk.Label(label="Privacy"))

        # --- About tab ---
        about_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        about_box.set_border_width(8)
        about_lbl = Gtk.Label(label=f"{APP_TITLE if 'APP_TITLE' in globals() else 'DragonOS'}\nVersion: 0.1\n\nSimple modern settings dialog.\n")
        about_lbl.set_justify(Gtk.Justification.CENTER)
        about_box.pack_start(about_lbl, True, True, 0)

        emit_test = Gtk.Button(label="Test notification")
        emit_test.connect("clicked", self._on_test_notification)
        about_box.pack_start(emit_test, False, False, 0)

        nb.append_page(about_box, Gtk.Label(label="About"))

        # --- action area (Apply / Cancel) ---
        self.add_button("Cancel", Gtk.ResponseType.CANCEL)
        apply_btn = self.add_button("Apply", Gtk.ResponseType.APPLY)
        apply_btn.get_style_context().add_class("suggested-action")
        self.set_default_response(Gtk.ResponseType.APPLY)
        self.connect("response", self._on_response)

        # small CSS to make things nicer
        self._apply_css()

    # ---------------- prefs I/O ----------------
    def _load_prefs(self):
        try:
            if not CONFIG_DIR.exists():
                CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            if CONFIG_FILE.exists():
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                # merge defaults
                prefs = DEFAULTS.copy()
                # deep for privacy
                prefs.update({k: v for k, v in data.items() if k != "privacy"})
                prefs["privacy"] = DEFAULTS["privacy"].copy()
                if "privacy" in data and isinstance(data["privacy"], dict):
                    prefs["privacy"].update(data["privacy"])
            else:
                prefs = DEFAULTS.copy()
            self._prefs = prefs
        except Exception:
            self._prefs = DEFAULTS.copy()

        # apply to widgets
        # language: set active by code prefix
        lang_widget = self._widgets.get('language')
        if lang_widget:
            # find matching index
            code = self._prefs.get("language", DEFAULTS["language"])
            found = 0
            for i in range(lang_widget.get_active() + 10):  # iterate safely
                try:
                    txt = lang_widget.get_active_text()
                except Exception:
                    break
                break
            # simple set: pick by text startswith code
            for idx in range(0, 10):
                try:
                    text = lang_widget.get_nth_text(idx)
                    if not text:
                        break
                    if text.startswith(code):
                        lang_widget.set_active(idx)
                        break
                except Exception:
                    break

        # theme
        try:
            th = self._prefs.get("theme", DEFAULTS["theme"])
            thw = self._widgets.get("theme")
            if thw:
                # set by text match
                for i in range(0, 10):
                    try:
                        t = thw.get_nth_text(i)
                        if not t:
                            break
                        if t == th:
                            thw.set_active(i); break
                    except Exception:
                        break
        except Exception:
            pass

        # switches
        try:
            self._widgets['snap_to_grid'].set_active(bool(self._prefs.get("snap_to_grid", True)))
            self._widgets['show_clock'].set_active(bool(self._prefs.get("show_clock", True)))
            self._widgets['telemetry'].set_active(bool(self._prefs.get("privacy", {}).get("telemetry", False)))
            self._widgets['auto_updates'].set_active(bool(self._prefs.get("privacy", {}).get("auto_updates", True)))
        except Exception:
            pass

        # wallpaper
        try:
            if 'wallpaper' in self._widgets and self._prefs.get("wallpaper"):
                fp = self._widgets['wallpaper']
                try:
                    fp.set_filename(self._prefs.get("wallpaper"))
                except Exception:
                    # ignore
                    pass
        except Exception:
            pass

    def _gather_prefs_from_ui(self):
        prefs = {}
        # language (take code prefix)
        try:
            lang_w = self._widgets.get('language')
            if lang_w:
                txt = lang_w.get_active_text() or ""
                prefs['language'] = txt.split(" ", 1)[0] if txt else DEFAULTS['language']
        except Exception:
            prefs['language'] = DEFAULTS['language']

        # theme
        try:
            thw = self._widgets.get('theme')
            prefs['theme'] = thw.get_active_text() if thw else DEFAULTS['theme']
        except Exception:
            prefs['theme'] = DEFAULTS['theme']

        # switches
        prefs['snap_to_grid'] = bool(self._widgets.get('snap_to_grid').get_active()) if self._widgets.get('snap_to_grid') else DEFAULTS['snap_to_grid']
        prefs['show_clock'] = bool(self._widgets.get('show_clock').get_active()) if self._widgets.get('show_clock') else DEFAULTS['show_clock']

        # wallpaper
        try:
            wp = self._widgets.get('wallpaper')
            if wp:
                fn = wp.get_filename()
                prefs['wallpaper'] = fn if fn else ""
            else:
                prefs['wallpaper'] = ""
        except Exception:
            prefs['wallpaper'] = ""

        # privacy
        prefs['privacy'] = {
            "telemetry": bool(self._widgets.get('telemetry').get_active()) if self._widgets.get('telemetry') else DEFAULTS['privacy']['telemetry'],
            "auto_updates": bool(self._widgets.get('auto_updates').get_active()) if self._widgets.get('auto_updates') else DEFAULTS['privacy']['auto_updates']
        }
        return prefs

    def _save_prefs(self, prefs):
        """
        Non-blocking save; we schedule via GLib.idle_add to avoid UI freeze.
        """
        def _do_save():
            try:
                if not CONFIG_DIR.exists():
                    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
                with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                    json.dump(prefs, f, ensure_ascii=False, indent=2)
            except Exception:
                pass
            return False
        GLib.idle_add(_do_save)

    # ---------------- widget callbacks ----------------
    def _on_wallpaper_selected(self, btn):
        # immediate preview if parent supports wallpaper
        try:
            fn = btn.get_filename()
            if fn and self.parent and hasattr(self.parent, "wallpaper") and callable(getattr(self.parent.wallpaper, "set_wallpaper")):
                try:
                    # set wallpaper on parent for live preview
                    self.parent.wallpaper.set_wallpaper(fn)
                except Exception:
                    pass
        except Exception:
            pass

    def _on_preview_wallpaper(self, _btn):
        try:
            wp = self._widgets.get('wallpaper')
            if wp:
                fn = wp.get_filename()
                if fn and os.path.exists(fn) and self.parent and hasattr(self.parent, "wallpaper"):
                    try:
                        self.parent.wallpaper.set_wallpaper(fn)
                    except Exception:
                        pass
                else:
                    info = Gtk.MessageDialog(transient_for=self, flags=0,
                                             message_type=Gtk.MessageType.INFO, buttons=Gtk.ButtonsType.OK,
                                             text="Wallpaper preview")
                    info.format_secondary_text("Please choose an existing image file to preview.")
                    info.run(); info.destroy()
        except Exception:
            pass

    def _on_test_notification(self, _btn):
        # simple infobar/messagebox as "notification" simulation
        dlg = Gtk.MessageDialog(transient_for=self, flags=0,
                                message_type=Gtk.MessageType.INFO, buttons=Gtk.ButtonsType.OK,
                                text="Test Notification")
        dlg.format_secondary_text("This is a test notification from Settings.")
        dlg.run(); dlg.destroy()

    # ---------------- response handler ----------------
    def _on_response(self, dialog, resp):
        if resp == Gtk.ResponseType.CANCEL:
            # restore preview wallpaper from prefs (if parent exists)
            try:
                if self.parent and hasattr(self.parent, "wallpaper") and self._prefs.get("wallpaper"):
                    try:
                        self.parent.wallpaper.set_wallpaper(self._prefs.get("wallpaper"))
                    except Exception:
                        pass
            except Exception:
                pass
            self.destroy()
            return

        if resp == Gtk.ResponseType.APPLY:
            # gather and save
            new_prefs = self._gather_prefs_from_ui()
            self._save_prefs(new_prefs)
            # call apply callback if provided
            try:
                if callable(self.on_apply_callback):
                    # schedule the callback after save
                    GLib.idle_add(lambda: (self.on_apply_callback(new_prefs), False))
            except Exception:
                pass
            # also update in-memory prefs
            self._prefs = new_prefs
            # subtle feedback
            info = Gtk.MessageDialog(transient_for=self, flags=0,
                                    message_type=Gtk.MessageType.INFO, buttons=Gtk.ButtonsType.OK,
                                    text="Sozlamalar saqlandi")
            info.format_secondary_text("Changes have been applied.")
            info.run(); info.destroy()
            # keep dialog open? we'll close it
            self.destroy()
            return

    # ---------------- helpers ----------------
    def _apply_css(self):
        css = b"""
        dialog { background: #0b0e13; color: #e6eef8; }
        label { color: #dbe8ff; }
        .suggested-action { background-color: #6a79ff; color: #fff; border-radius: 6px; }
        """
        try:
            prov = Gtk.CssProvider()
            prov.load_from_data(css)
            screen = Gdk.Screen.get_default()
            Gtk.StyleContext.add_provider_for_screen(screen, prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        except Exception:
            pass

# ---------------- Example usage ----------------
if __name__ == "__main__":
    # quick demo
    class FakeParent(Gtk.Window):
        def __init__(self):
            super().__init__(title="Fake Desktop")
            self.set_default_size(300,200)
            self.wallpaper = None  # no wallpaper in demo
            btn = Gtk.Button(label="Open Settings")
            btn.connect("clicked", lambda *_: SettingsDialog(self, on_apply_callback=self.on_apply).run())
            self.add(btn)
            self.show_all()
        def on_apply(self, prefs):
            print("Applied prefs:", prefs)

    app = Gtk.Application()
    def on_activate(app):
        w = FakeParent()
        w.set_application(app)
        w.show_all()
    app.connect("activate", on_activate)
    app.run(None)
