# apps.py
import gi, os, stat
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GdkPixbuf, Pango

import shutil

# ---------- Notepad ----------
class Notepad(Gtk.Box):
    """
    Modern minimal Notepad widget:
    - header with filename entry, Save, Save as, Autosave toggle
    - TextView editor with monospace via CSS
    - status label with saved / error messages
    - Ctrl+S / Ctrl+Shift+S shortcuts
    """
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.set_border_width(8)

        # --- Header (filename + actions) ---
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header.set_halign(Gtk.Align.FILL)
        header.set_valign(Gtk.Align.START)

        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text("Fayl nomi (ixtiyoriy).txt")
        self.entry.set_hexpand(True)
        header.pack_start(self.entry, True, True, 0)

        # Save button (icon if available)
        self.save_btn = Gtk.Button()
        self.save_btn.set_tooltip_text("Save (Ctrl+S)")
        self.save_btn.set_relief(Gtk.ReliefStyle.NONE)
        try:
            self.save_btn.set_image(load_icon("document-save-symbolic", size=16))
        except Exception:
            self.save_btn.set_label("Save")
        self.save_btn.connect("clicked", self.on_save)
        header.pack_start(self.save_btn, False, False, 0)

        # Save as
        self.saveas_btn = Gtk.Button()
        self.saveas_btn.set_tooltip_text("Save As (Ctrl+Shift+S)")
        self.saveas_btn.set_relief(Gtk.ReliefStyle.NONE)
        try:
            self.saveas_btn.set_image(load_icon("document-save-as-symbolic", size=16))
        except Exception:
            self.saveas_btn.set_label("Save as")
        self.saveas_btn.connect("clicked", self.on_save_as)
        header.pack_start(self.saveas_btn, False, False, 0)

        # Autosave toggle
        self.autosave_toggle = Gtk.ToggleButton(label="Autosave")
        self.autosave_toggle.set_tooltip_text("Autosave every 8s when enabled")
        self.autosave_toggle.connect("toggled", self.on_autosave_toggled)
        header.pack_start(self.autosave_toggle, False, False, 0)

        self.pack_start(header, False, False, 0)

        # --- Editor (TextView inside ScrolledWindow) ---
        self.view = Gtk.TextView()
        self.view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.view.get_buffer().connect("changed", self._on_buffer_changed)

        sc = Gtk.ScrolledWindow()
        sc.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        sc.add(self.view)
        sc.set_min_content_height(260)
        self.pack_start(sc, True, True, 0)

        # --- Status bar: saved / wordcount ---
        status = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        status.set_halign(Gtk.Align.FILL)
        self.status_lbl = Gtk.Label(label="")  # temporary messages (Saved / Error)
        self.status_lbl.get_style_context().add_class("notepad-status")
        self.wordcount_lbl = Gtk.Label(label="0 words • 0 chars")
        self.wordcount_lbl.set_xalign(1.0)

        status.pack_start(self.status_lbl, True, True, 0)
        status.pack_end(self.wordcount_lbl, False, False, 0)
        self.pack_start(status, False, False, 0)

        # internal state
        self._autosave_id = None
        self._status_timeout_id = None

        # keyboard shortcuts on the TextView
        self.view.connect("key-press-event", self._on_key_press)

        # apply clean CSS
        self._apply_css()

    # ---------------- CSS ----------------
    def _apply_css(self):
        css = b"""
        /* Notepad minimal styling */
        entry, textview { background: rgba(255,255,255,0.02); color: #e6eef6; border-radius: 8px; padding: 8px; }
        textview { font-family: monospace; font-size: 13px; }
        button { background: transparent; border: none; color: #cfe0ff; padding: 6px; }
        .notepad-status { color: #9fb3ff; font-size: 11px; padding: 4px; }
        """
        prov = Gtk.CssProvider()
        prov.load_from_data(css)
        screen = Gdk.Screen.get_default()
        Gtk.StyleContext.add_provider_for_screen(screen, prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    # ---------------- Buffer / wordcount ----------------
    def _on_buffer_changed(self, buf):
        start, end = buf.get_start_iter(), buf.get_end_iter()
        text = buf.get_text(start, end, True) or ""
        chars = len(text)
        words = len([w for w in text.split() if w.strip()])
        self.wordcount_lbl.set_text(f"{words} words • {chars} chars")

    # ---------------- Keyboard ----------------
    def _on_key_press(self, widget, ev):
        # handle Ctrl+S and Ctrl+Shift+S
        name = Gdk.keyval_name(ev.keyval) or ""
        state = ev.state
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        if ctrl and name.lower() == "s":
            if shift:
                self.on_save_as(None)
            else:
                self.on_save(None)
            return True
        return False

    # ---------------- Save / Save as ----------------
    def on_save(self, *_):
        """Save to filename in entry; if empty prompt Save As."""
        name = self.entry.get_text().strip()
        if not name:
            # prompt Save As
            self.on_save_as(None)
            return

        # ensure directory exists if path provided
        try:
            dirname = os.path.dirname(name)
            if dirname:
                os.makedirs(dirname, exist_ok=True)
        except Exception:
            pass

        self._write_to_file(name)

    def on_save_as(self, *_):
        parent = self.get_toplevel()
        dlg = Gtk.FileChooserDialog(
            title="Save As",
            parent=parent if isinstance(parent, Gtk.Window) else None,
            action=Gtk.FileChooserAction.SAVE,
        )
        dlg.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_SAVE, Gtk.ResponseType.OK)
        dlg.set_do_overwrite_confirmation(True)
        # suggested filename
        suggested = self.entry.get_text().strip() or "note.txt"
        dlg.set_current_name(suggested)

        # filter
        flt = Gtk.FileFilter(); flt.set_name("Text files"); flt.add_pattern("*.txt"); dlg.add_filter(flt)

        resp = dlg.run()
        if resp == Gtk.ResponseType.OK:
            filename = dlg.get_filename()
            self.entry.set_text(filename)
            self._write_to_file(filename)
        dlg.destroy()

    def _write_to_file(self, filename):
        buf = self.view.get_buffer()
        start, end = buf.get_start_iter(), buf.get_end_iter()
        text = buf.get_text(start, end, True)
        try:
            with open(filename, "w", encoding="utf-8") as f:
                f.write(text)
            # show saved status briefly
            self._flash_status(f"Saqlandi: {os.path.basename(filename)}")
        except Exception as e:
            info_dialog(self.get_toplevel(), "Xatolik", str(e))

    # ---------------- Autosave ----------------
    def on_autosave_toggled(self, btn):
        if btn.get_active():
            # enable autosave every 8 seconds
            if self._autosave_id is None:
                self._autosave_id = GLib.timeout_add_seconds(8, self._do_autosave)
            self._flash_status("Autosave: ON")
        else:
            if self._autosave_id is not None:
                GLib.source_remove(self._autosave_id)
                self._autosave_id = None
            self._flash_status("Autosave: OFF")

    def _do_autosave(self):
        name = self.entry.get_text().strip()
        if name:
            self._write_to_file(name)
        else:
            # do nothing (or you could implement saving to temp)
            pass
        # keep the timeout alive
        return True

    # ---------------- Status flashing ----------------
    def _flash_status(self, text, timeout=2200):
        # cancel previous timeout
        try:
            if self._status_timeout_id is not None:
                GLib.source_remove(self._status_timeout_id)
                self._status_timeout_id = None
        except Exception:
            pass
        self.status_lbl.set_text(text)
        # clear after timeout ms
        self._status_timeout_id = GLib.timeout_add(timeout, self._clear_status)
        return True

    def _clear_status(self):
        try:
            self.status_lbl.set_text("")
        finally:
            self._status_timeout_id = None
        return False