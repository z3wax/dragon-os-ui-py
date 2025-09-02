from appsz.Calcul import Calculator
from appsz.Notepad import Notepad
from appsz.MusicPlayer import MusicPlayer
from appsz.FileManager import FileManager
# from appsz.SettingsDialog import SettingsDialog
from appsz.Gal import Gallery

# SettingsDialog — improved, robust implementation
import os
import json
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib

# helpers expected in your project (from PART1)
try:
    from helpers import load_icon, info_dialog
except Exception:
    # safe fallbacks if helpers are missing (simple implementations)
    def load_icon(name_or_path, size=48):
        img = Gtk.Image()
        try:
            # if looks like a path, try load file
            if os.path.exists(str(name_or_path)):
                pb = GdkPixbuf.Pixbuf.new_from_file_at_size(str(name_or_path), size, size)
                img.set_from_pixbuf(pb)
            else:
                img.set_from_icon_name(str(name_or_path), Gtk.IconSize.DIALOG)
        except Exception:
            try:
                img.set_from_icon_name("image-x-generic", Gtk.IconSize.DIALOG)
            except Exception:
                pass
        return img

    def info_dialog(parent, title, message):
        try:
            dlg = Gtk.MessageDialog(transient_for=parent, flags=0, message_type=Gtk.MessageType.INFO,
                                    buttons=Gtk.ButtonsType.OK, text=title)
            dlg.format_secondary_text(str(message))
            dlg.run()
            dlg.destroy()
        except Exception:
            print(title, message)

SETTINGS_PATH = os.path.expanduser("~/.config/dragonos/settings.json")

def _ensure_settings_dir():
    d = os.path.dirname(SETTINGS_PATH)
    os.makedirs(d, exist_ok=True)


class SettingsDialog:
    """
    Modern Settings dialog.
    parent should be your Desktop instance (it must implement: toggle_dark, wallpaper,
    apply_dark_icon_backdrop, and have `desktop_items` mapping usable for icon-size updates).
    """

    def __init__(self, parent):
        self.parent = parent
        self.preview_path = None
        self._loaded = False
        # lazy read module globals
        self._read_current_globals()

    def _read_current_globals(self):
        try:
            import oos_ui
            self._global_icon_size = getattr(oos_ui, "ICON_SIZE", 56)
            self._global_snap = getattr(oos_ui, "SNAP_TO_GRID", True)
        except Exception:
            self._global_icon_size = 56
            self._global_snap = True

    def _load_settings_file(self):
        try:
            if os.path.exists(SETTINGS_PATH):
                with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def _save_settings_file(self, d):
        try:
            _ensure_settings_dir()
            with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=2)
        except Exception as e:
            # non-fatal but useful to know
            print("Settings save failed:", e)

    def run(self):
        saved = self._load_settings_file()

        dlg = Gtk.Dialog(title="Settings", transient_for=self.parent, flags=0)
        dlg.add_button(Gtk.STOCK_CLOSE, Gtk.ResponseType.CLOSE)
        dlg.add_button("Save & Close", Gtk.ResponseType.APPLY)
        dlg.add_button("Apply", Gtk.ResponseType.OK)
        dlg.set_default_size(720, 480)

        content = dlg.get_content_area()
        content.set_border_width(12)

        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        content.pack_start(hbox, True, True, 0)

        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        left.set_size_request(400, -1)
        hbox.pack_start(left, False, False, 0)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        hbox.pack_start(right, True, True, 0)

        # Dark mode
        row = Gtk.Box(spacing=8)
        lab = Gtk.Label(label="Dark mode")
        lab.set_xalign(0)
        row.pack_start(lab, True, True, 0)
        dark_switch = Gtk.Switch()
        try:
            cur = Gtk.Settings.get_default().get_property("gtk-application-prefer-dark-theme")
            dark_switch.set_active(bool(saved.get("dark_mode", cur)))
        except Exception:
            dark_switch.set_active(bool(saved.get("dark_mode", False)))
        row.pack_start(dark_switch, False, False, 0)
        left.pack_start(row, False, False, 0)

        # Wallpaper: preview small + buttons
        wrow = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        wlab = Gtk.Label(label="Wallpaper")
        wlab.set_xalign(0)
        wrow.pack_start(wlab, False, False, 0)

        self.wp_preview = Gtk.Image()
        try:
            self.wp_preview.set_pixel_size(160)
        except Exception:
            pass
        preview_frame = Gtk.Frame(); preview_frame.set_shadow_type(Gtk.ShadowType.IN)
        preview_frame.add(self.wp_preview)
        wrow.pack_start(preview_frame, False, False, 0)

        wp_btns = Gtk.Box(spacing=6)
        pick_btn = Gtk.Button(label="Pick image…")
        pick_btn.connect("clicked", lambda *_: self._on_pick_wallpaper())
        wp_btns.pack_start(pick_btn, False, False, 0)

        apply_wp_btn = Gtk.Button(label="Apply wallpaper")
        apply_wp_btn.connect("clicked", lambda *_: self._apply_wallpaper())
        wp_btns.pack_start(apply_wp_btn, False, False, 0)

        reset_wp_btn = Gtk.Button(label="Reset to default")
        reset_wp_btn.connect("clicked", lambda *_: self._reset_wallpaper())
        wp_btns.pack_start(reset_wp_btn, False, False, 0)

        wrow.pack_start(wp_btns, False, False, 0)
        left.pack_start(wrow, False, False, 0)

        # Parallax toggle
        prow = Gtk.Box(spacing=8)
        plab = Gtk.Label(label="Wallpaper parallax"); plab.set_xalign(0)
        prow.pack_start(plab, True, True, 0)
        parallax_switch = Gtk.Switch()
        try:
            parallax_switch.set_active(bool(saved.get("parallax", getattr(self.parent.wallpaper, "_parallax_enabled", True))))
        except Exception:
            parallax_switch.set_active(bool(saved.get("parallax", True)))
        prow.pack_start(parallax_switch, False, False, 0)
        left.pack_start(prow, False, False, 0)

        # Snap-to-grid
        srow = Gtk.Box(spacing=8)
        slab = Gtk.Label(label="Snap icons to grid"); slab.set_xalign(0)
        srow.pack_start(slab, True, True, 0)
        snap_switch = Gtk.Switch()
        snap_switch.set_active(bool(saved.get("snap_to_grid", self._global_snap)))
        srow.pack_start(snap_switch, False, False, 0)
        left.pack_start(srow, False, False, 0)

        # Icon size
        irow = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        ilab = Gtk.Label(label="Icon size")
        ilab.set_xalign(0)
        irow.pack_start(ilab, False, False, 0)
        icon_adjust = Gtk.Adjustment(value=int(saved.get("icon_size", self._global_icon_size)), lower=24, upper=128, step_increment=4, page_increment=8)
        icon_scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=icon_adjust)
        icon_scale.set_draw_value(True)
        icon_scale.set_value_pos(Gtk.PositionType.RIGHT)
        irow.pack_start(icon_scale, False, False, 0)
        left.pack_start(irow, False, False, 0)

        # Version / footer
        ver = Gtk.Label(label="DragonOS — Minimal UI settings")
        ver.set_xalign(0)
        left.pack_end(ver, False, False, 0)

        # RIGHT: Preview & live demo
        rp_label = Gtk.Label(label="Preview & Live")
        rp_label.get_style_context().add_class("title")
        rp_label.set_xalign(0)
        right.pack_start(rp_label, False, False, 0)

        self.wp_preview_large = Gtk.Image()
        try:
            self.wp_preview_large.set_pixel_size(320)
        except Exception:
            pass
        big_frame = Gtk.Frame(); big_frame.set_shadow_type(Gtk.ShadowType.NONE)
        big_frame.add(self.wp_preview_large)
        right.pack_start(big_frame, True, True, 0)

        # Icon size demo
        demo_box = Gtk.Box(spacing=12)
        demo_box.set_halign(Gtk.Align.START)
        demo_label = Gtk.Label(label="Icon size demo:"); demo_label.set_xalign(0)
        demo_box.pack_start(demo_label, False, False, 0)

        demo_icon = load_icon("folder", size=int(icon_adjust.get_value()))
        demo_box.pack_start(demo_icon, False, False, 0)

        right.pack_start(demo_box, False, False, 0)

        # initial wallpaper preview from parent or saved
        try:
            cur_path = saved.get("wallpaper", None)
            if not cur_path:
                cur_path = getattr(self.parent.wallpaper, "_path", None)
            if cur_path:
                self.preview_path = cur_path
                self._set_wallpaper_preview(cur_path)
        except Exception:
            pass

        # live icon-size update handler
        def _on_icon_size_change(scale):
            v = int(scale.get_value())
            try:
                new_demo = load_icon("folder", size=v)
                # replace demo_icon in demo_box
                children = demo_box.get_children()
                if len(children) >= 2:
                    old = children[1]
                    demo_box.remove(old)
                    demo_box.pack_start(new_demo, False, False, 0)
                else:
                    demo_box.pack_start(new_demo, False, False, 0)
                new_demo.show_all()
            except Exception:
                try:
                    demo_icon.set_pixel_size(v)
                except Exception:
                    pass
            # resize wallpaper preview
            try:
                self.wp_preview_large.set_pixel_size(max(120, v * 4))
            except Exception:
                pass

        icon_scale.connect("value-changed", lambda s: _on_icon_size_change(s))

        # Show and handle responses in a loop
        dlg.show_all()
        while True:
            resp = dlg.run()
            if resp == Gtk.ResponseType.OK:
                # Apply (do not close)
                self._apply_values(
                    dark_on=dark_switch.get_active(),
                    parallax_on=parallax_switch.get_active(),
                    snap_on=snap_switch.get_active(),
                    icon_size=int(icon_scale.get_value()),
                    preview_path=self.preview_path,
                )
                # continue showing dialog
                continue
            elif resp == Gtk.ResponseType.APPLY:
                # Apply + Save & Close
                self._apply_values(
                    dark_on=dark_switch.get_active(),
                    parallax_on=parallax_switch.get_active(),
                    snap_on=snap_switch.get_active(),
                    icon_size=int(icon_scale.get_value()),
                    preview_path=self.preview_path,
                )
                settings = {
                    "dark_mode": bool(dark_switch.get_active()),
                    "parallax": bool(parallax_switch.get_active()),
                    "snap_to_grid": bool(snap_switch.get_active()),
                    "icon_size": int(icon_scale.get_value()),
                    "wallpaper": self.preview_path or None,
                }
                self._save_settings_file(settings)
                break
            else:
                # CLOSE or other -> just exit (no save)
                break

        dlg.destroy()

    # ---------------- helpers ----------------
    def _on_pick_wallpaper(self):
        dlg = Gtk.FileChooserDialog(title="Pick Wallpaper", parent=self.parent, action=Gtk.FileChooserAction.OPEN)
        dlg.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OPEN, Gtk.ResponseType.OK)
        flt = Gtk.FileFilter(); flt.set_name("Images")
        for p in ("*.png", "*.jpg", "*.jpeg", "*.webp", "*.bmp"):
            flt.add_pattern(p)
        dlg.add_filter(flt)
        if dlg.run() == Gtk.ResponseType.OK:
            fp = dlg.get_filename()
            if fp and os.path.exists(fp):
                self.preview_path = fp
                self._set_wallpaper_preview(fp)
        dlg.destroy()

    def _set_wallpaper_preview(self, path):
        try:
            # larger preview
            pb = GdkPixbuf.Pixbuf.new_from_file_at_size(path, 640, 360)
            self.wp_preview_large.set_from_pixbuf(pb)
            # small preview
            try:
                pb2 = GdkPixbuf.Pixbuf.new_from_file_at_size(path, 160, 90)
                self.wp_preview.set_from_pixbuf(pb2)
            except Exception:
                self.wp_preview.set_from_pixbuf(pb)
        except Exception:
            # fallback icons
            try:
                self.wp_preview.set_from_icon_name("image-x-generic", Gtk.IconSize.DIALOG)
                self.wp_preview_large.set_from_icon_name("image-x-generic", Gtk.IconSize.DIALOG)
            except Exception:
                pass

    def _apply_wallpaper(self):
        if not self.preview_path:
            info_dialog(self.parent, "Wallpaper", "No wallpaper selected.")
            return
        try:
            self.parent.wallpaper.set_wallpaper(self.preview_path)
            # quick luminance heuristic
            try:
                pb = GdkPixbuf.Pixbuf.new_from_file_at_size(self.preview_path, 8, 8)
                pixels = pb.get_pixels()
                ch = pb.get_n_channels()
                stride = pb.get_rowstride()
                total = 0.0; cnt = 0
                for yy in range(pb.get_height()):
                    ro = yy * stride
                    for xx in range(pb.get_width()):
                        off = ro + xx * ch
                        r = pixels[off]; g = pixels[off+1]; b = pixels[off+2]
                        lum = (0.2126*r + 0.7152*g + 0.0722*b) / 255.0
                        total += lum; cnt += 1
                avg = (total / cnt) if cnt else 0.5
                try:
                    self.parent.apply_dark_icon_backdrop(avg > 0.6)
                except Exception:
                    pass
            except Exception:
                pass
            info_dialog(self.parent, "Wallpaper", "Wallpaper applied")
        except Exception as e:
            info_dialog(self.parent, "Wallpaper", f"Could not apply wallpaper:\n{e}")

    def _reset_wallpaper(self):
        try:
            self.parent.wallpaper.set_wallpaper(None)
            try:
                self.parent.apply_dark_icon_backdrop(False)
            except Exception:
                pass
            self.preview_path = None
            try:
                self.wp_preview.set_from_icon_name("applications-graphics", Gtk.IconSize.DIALOG)
                self.wp_preview_large.set_from_icon_name("applications-graphics", Gtk.IconSize.DIALOG)
            except Exception:
                pass
            info_dialog(self.parent, "Wallpaper", "Reset to default")
        except Exception as e:
            info_dialog(self.parent, "Wallpaper", f"Error:\n{e}")

    def _apply_values(self, dark_on, parallax_on, snap_on, icon_size, preview_path=None):
        # Dark
        try:
            # prefer using parent's toggle_dark if present
            if hasattr(self.parent, "toggle_dark"):
                self.parent.toggle_dark(bool(dark_on))
            else:
                Gtk.Settings.get_default().set_property("gtk-application-prefer-dark-theme", bool(dark_on))
        except Exception:
            pass

        # Parallax
        try:
            if hasattr(self.parent, "wallpaper") and hasattr(self.parent.wallpaper, "set_parallax"):
                self.parent.wallpaper.set_parallax(bool(parallax_on))
        except Exception:
            pass

        # Wallpaper (if provided)
        if preview_path:
            try:
                if hasattr(self.parent, "wallpaper"):
                    self.parent.wallpaper.set_wallpaper(preview_path)
            except Exception:
                pass

        # Snap-to-grid and ICON_SIZE global
        try:
            import oos_ui
            setattr(oos_ui, "SNAP_TO_GRID", bool(snap_on))
            setattr(oos_ui, "ICON_SIZE", int(icon_size))
        except Exception:
            pass

        # Update desktop icons sizes immediately
        try:
            desktop_items = getattr(self.parent, "desktop_items", {})
            for k, v in list(desktop_items.items()):
                box, path, is_app, app_name = v
                # inner vbox -> first child is image
                try:
                    child = box.get_child()
                    if child and isinstance(child, Gtk.Box):
                        kids = child.get_children()
                        if kids:
                            img_widget = kids[0]
                            new_img = None
                            # determine icon name
                            icon_name = None
                            if is_app and app_name:
                                # try to get icon ref from parent's APPS
                                icon_name = getattr(self.parent, "APPS", {}).get(app_name, "application-x-executable")
                            else:
                                if os.path.isdir(path):
                                    icon_name = "folder"
                                else:
                                    _, ext = os.path.splitext(path.lower())
                                    image_exts = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
                                    video_exts = (".mp4", ".mov", ".mkv", ".webm", ".avi")
                                    if ext in image_exts:
                                        icon_name = "image-x-generic"
                                    elif ext in video_exts:
                                        icon_name = "video-x-generic"
                                    else:
                                        icon_name = "text-x-generic"
                            try:
                                new_img = load_icon(icon_name, size=int(icon_size))
                            except Exception:
                                new_img = load_icon(icon_name, size=int(icon_size))
                            if new_img and isinstance(img_widget, Gtk.Image):
                                # try to copy pixbuf if available
                                try:
                                    pb = None
                                    if hasattr(new_img, "get_pixbuf"):
                                        pb = new_img.get_pixbuf()
                                    if pb:
                                        img_widget.set_from_pixbuf(pb)
                                    else:
                                        # replace widget
                                        child.remove(img_widget)
                                        child.pack_start(new_img, False, False, 0)
                                except Exception:
                                    try:
                                        child.remove(img_widget)
                                        child.pack_start(new_img, False, False, 0)
                                    except Exception:
                                        pass
                                child.show_all()
                except Exception:
                    pass
        except Exception:
            pass

        # done — no auto-save here (Save & Close persists)
        return
