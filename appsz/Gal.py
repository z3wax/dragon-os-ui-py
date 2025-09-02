# apps.py
import gi, os, stat
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GdkPixbuf, Pango
from appsz.Notepad import Notepad
from appsz.Calcul import Calculator
from appsz import *
import shutil

from helpers import info_dialog, make_target_entries, load_icon, ensure_desktop_folder
# Gallery iOS-like preview (GTK3)
import os
import sys
import glob
import shutil
import subprocess


import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib

from helpers import load_icon, info_dialog

IMAGE_PATTERNS = ["*.png", "*.jpg", "*.jpeg", "*.webp", "*.bmp", "*.gif", "*.tiff"]

class Gallery(Gtk.Box):
    """
    iOS-like preview Gallery widget (GTK3-friendly).
    - FlowBox thumbnails with hover zoom, lazy thumbnail generation
    - Fullscreen translucent backdrop + centered 'card' preview (iPhone-like)
    """
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        try: self.set_border_width(10)
        except Exception: pass
        try: self.set_hexpand(True); self.set_vexpand(True)
        except Exception: pass

        # state
        self.images = []
        self.current_index = 0
        self._thumb_cache = {}
        self._loading_idle = None
        self._slideshow_id = None
        self._viewer_scale = 1.0
        self._viewer_rotation = 0
        self._viewer_pixbuf_original = None

        # top bar
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label="Gallery")
        title.set_xalign(0)
        title.get_style_context().add_class("g-title")
        top.pack_start(title, True, True, 0)

        open_btn = Gtk.Button(); open_btn.set_relief(Gtk.ReliefStyle.NONE)
        open_btn.set_tooltip_text("Open folder")
        try: open_btn.set_image(load_icon("folder", size=18))
        except Exception: open_btn.set_label("Open")
        open_btn.connect("clicked", self.on_open_folder)
        top.pack_start(open_btn, False, False, 0)

        refresh_btn = Gtk.Button(); refresh_btn.set_relief(Gtk.ReliefStyle.NONE)
        refresh_btn.set_tooltip_text("Refresh")
        try: refresh_btn.set_image(load_icon("view-refresh", size=18))
        except Exception: refresh_btn.set_label("⟳")
        refresh_btn.connect("clicked", lambda *_: self.reload_current_folder())
        top.pack_start(refresh_btn, False, False, 0)

        self.pack_start(top, False, False, 0)

        # thumbnails (FlowBox in ScrolledWindow)
        self.scrolled = Gtk.ScrolledWindow()
        self.scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        try:
            self.scrolled.set_hexpand(True); self.scrolled.set_vexpand(True)
            self.scrolled.set_min_content_height(300)
        except Exception: pass

        self.flow = Gtk.FlowBox()
        try:
            self.flow.set_max_children_per_line(6)
            self.flow.set_row_spacing(12)
            self.flow.set_column_spacing(12)
            try: self.flow.set_selection_mode(Gtk.SelectionMode.SINGLE)
            except Exception: pass
            try: self.flow.connect("child-activated", self._on_thumb_activated)
            except Exception: pass
        except Exception: pass

        # GTK3 differences: prefer add, fallback to add_with_viewport
        try:
            self.scrolled.add(self.flow)
        except Exception:
            try: self.scrolled.add_with_viewport(self.flow)
            except Exception:
                try:
                    vp = Gtk.Viewport(); vp.add(self.flow); self.scrolled.add(vp)
                except Exception:
                    pass

        self.pack_start(self.scrolled, True, True, 0)

        # compact action row (styled circles)
        quick = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        quick.set_halign(Gtk.Align.CENTER)

        btn_play = Gtk.Button(); btn_play.set_tooltip_text("Start slideshow")
        try: btn_play.set_image(load_icon("media-playback-start", size=16))
        except Exception: btn_play.set_label("▶")
        btn_play.connect("clicked", self._toggle_slideshow)
        btn_play.get_style_context().add_class("circle")
        quick.pack_start(btn_play, False, False, 0)

        for (label, icon, cb) in (("zoom in", "zoom-in", lambda *_: self._viewer_action_or_hint(self._viewer_zoom, 1.25)),
                                  ("zoom out", "zoom-out", lambda *_: self._viewer_action_or_hint(self._viewer_zoom, 0.8)),
                                  ("rotate", "object-rotate-right", lambda *_: self._viewer_action_or_hint(self._viewer_rotate, 90))):
            b = Gtk.Button(); b.set_tooltip_text(label)
            try: b.set_image(load_icon(icon, size=14))
            except Exception: b.set_label(label[0].upper())
            b.connect("clicked", cb)
            b.get_style_context().add_class("circle")
            quick.pack_start(b, False, False, 0)

        self.pack_start(quick, False, False, 0)

        # css
        self._apply_css()

        # auto-load pictures
        pics = os.path.expanduser("~/Pictures")
        if os.path.isdir(pics): self.load_folder(pics)

    # CSS — iOS-like translucent card + round thumbs + dot indicator
    def _apply_css(self):
        css = b"""
        .g-title { font-size: 16px; font-weight: 600; padding: 6px; color: rgba(0,0,0,0.6); }

        /* thumbnails */
        .thumb-frame { background: rgba(255,255,255,0.02); border-radius: 14px; padding: 8px; }
        .thumb-image { border-radius: 12px; min-width: 140px; min-height: 96px; }
        .thumb-frame:hover { background: rgba(255,255,255,0.06); transform: translateY(-6px); }

        /* action circles */
        .circle { border-radius: 20px; border: none; padding: 10px; min-width: 44px; min-height: 44px;
                   background: rgba(255,255,255,0.03); }
        .circle:hover { background: rgba(255,255,255,0.06); }

        /* viewer modal backdrop (full-window dark translucent) */
        .ios-backdrop { background: rgba(0,0,0,0.52); }

        /* central card */
        .ios-card { background: rgba(250,250,250,0.96); border-radius: 18px; padding: 12px;
                    border: 1px solid rgba(0,0,0,0.06); min-width: 720px; min-height: 420px; }

        .ios-image { border-radius: 14px; }

        /* toolbar inside card */
        .ios-toolbar { spacing: 8px; padding: 6px; }

        /* dot indicators */
        .dot { background: rgba(0,0,0,0.25); border-radius: 6px; min-width: 8px; min-height: 8px; margin: 2px; }
        .dot.active { background: rgba(0,0,0,0.7); }
        """
        try:
            prov = Gtk.CssProvider()
            prov.load_from_data(css)
            screen = Gdk.Screen.get_default()
            if screen:
                Gtk.StyleContext.add_provider_for_screen(screen, prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        except Exception:
            pass

    # --- Folder / thumbnails ---
    def on_open_folder(self, *_):
        parent = self.get_toplevel() if isinstance(self.get_toplevel(), Gtk.Window) else None
        dlg = Gtk.FileChooserDialog(title="Select pictures folder", parent=parent, action=Gtk.FileChooserAction.SELECT_FOLDER)
        dlg.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, "Open", Gtk.ResponseType.OK)
        if dlg.run() == Gtk.ResponseType.OK:
            folder = dlg.get_filename(); dlg.destroy(); self.load_folder(folder)
        else:
            dlg.destroy()

    def reload_current_folder(self):
        if not self.images: return
        current = os.path.dirname(self.images[0])
        if os.path.isdir(current): self.load_folder(current)

    def load_folder(self, folder):
        try:
            imgs = []
            for pat in IMAGE_PATTERNS:
                imgs.extend(glob.glob(os.path.join(folder, pat)))
            imgs = sorted(imgs, key=lambda p: os.path.basename(p).lower())
            self.images = imgs
            for k in list(self._thumb_cache.keys()):
                if k not in self.images: del self._thumb_cache[k]
            self._populate_thumbs()
            if self._loading_idle:
                try: GLib.source_remove(self._loading_idle)
                except Exception: pass
                self._loading_idle = None
            self._loading_idle = GLib.idle_add(self._fill_thumbnails, 0)
        except Exception as e:
            info_dialog(self.get_toplevel(), "Error", f"Cannot open folder:\n{e}")

    def _populate_thumbs(self):
        # clear
        try:
            for c in list(self.flow.get_children()):
                try: self.flow.remove(c)
                except Exception: pass
        except Exception: pass

        if not self.images:
            lbl = Gtk.Label(label="No images found")
            try: self.flow.add(lbl)
            except Exception: pass
            try: self.flow.show_all()
            except Exception: pass
            return

        thumb_target = 160
        for i, path in enumerate(self.images):
            frame = Gtk.EventBox()
            try: frame.set_visible_window(False)
            except Exception: pass
            frame.get_style_context().add_class("thumb-frame")

            img = Gtk.Image()
            try: img.set_size_request(thumb_target, int(thumb_target * 0.66))
            except Exception: pass
            img.get_style_context().add_class("thumb-image")
            try: frame.add(img)
            except Exception: pass

            frame._g_idx = i
            frame._g_path = path

            frame.connect("button-press-event", self._thumb_button_press)
            frame.connect("enter-notify-event", self._on_thumb_enter)
            frame.connect("leave-notify-event", self._on_thumb_leave)

            try: self.flow.add(frame)
            except Exception: pass

        try: self.flow.show_all()
        except Exception: pass

    def _fill_thumbnails(self, start_index=0):
        total = len(self.images)
        i = int(start_index)
        count = 0
        while i < total and count < 8:
            path = self.images[i]
            if path not in self._thumb_cache:
                try:
                    pb = GdkPixbuf.Pixbuf.new_from_file(str(path))
                    w,h = pb.get_width(), pb.get_height()
                    scale = min(1.0, float(200) / max(w,h))
                    nw,nh = max(1, int(w*scale)), max(1, int(h*scale))
                    thumb = pb.scale_simple(nw, nh, GdkPixbuf.InterpType.BILINEAR)
                    self._thumb_cache[path] = thumb
                    for child in self.flow.get_children():
                        if getattr(child, "_g_path", None) == path:
                            try:
                                img_widget = child.get_children()[0]
                            except Exception:
                                img_widget = None
                            if isinstance(img_widget, Gtk.Image):
                                try: img_widget.set_from_pixbuf(thumb)
                                except Exception: pass
                            break
                except Exception:
                    for child in self.flow.get_children():
                        if getattr(child, "_g_path", None) == path:
                            try:
                                img_widget = child.get_children()[0]
                            except Exception:
                                img_widget = None
                            if isinstance(img_widget, Gtk.Image):
                                try: img_widget.set_from_icon_name("image-missing", Gtk.IconSize.DIALOG)
                                except Exception: pass
                            break
            i += 1; count += 1

        if i < total:
            try: self._loading_idle = GLib.idle_add(self._fill_thumbnails, i)
            except Exception: self._loading_idle = None
            return False
        else:
            self._loading_idle = None
            return False

    # --- thumbnail interactions ---
    def _on_thumb_enter(self, widget, event):
        try:
            kids = widget.get_children()
            img = kids[0] if kids and isinstance(kids[0], Gtk.Image) else None
            path = getattr(widget, "_g_path", None)
            pb = self._thumb_cache.get(path)
            if img and pb:
                larger = pb.scale_simple(int(pb.get_width()*1.15), int(pb.get_height()*1.15), GdkPixbuf.InterpType.BILINEAR)
                try: img.set_from_pixbuf(larger)
                except Exception: pass
        except Exception: pass

    def _on_thumb_leave(self, widget, event):
        try:
            kids = widget.get_children()
            img = kids[0] if kids and isinstance(kids[0], Gtk.Image) else None
            path = getattr(widget, "_g_path", None)
            pb = self._thumb_cache.get(path)
            if img and pb:
                try: img.set_from_pixbuf(pb)
                except Exception: pass
        except Exception: pass

    def _thumb_button_press(self, widget, event):
        try:
            if event.type == Gdk.EventType._2BUTTON_PRESS and event.button == 1:
                idx = getattr(widget, "_g_idx", 0); self.open_viewer(idx); return True
        except Exception: pass
        try:
            if event.type == Gdk.EventType.BUTTON_PRESS and event.button == 1:
                try: self.flow.select_child(widget)
                except Exception: pass
                return True
        except Exception: pass
        try:
            if event.type == Gdk.EventType.BUTTON_PRESS and event.button == 3:
                return self._on_thumb_context(widget, event)
        except Exception: pass
        return False

    def _on_thumb_context(self, widget, event):
        path = getattr(widget, "_g_path", None)
        if not path: return False
        menu = Gtk.Menu()
        mi_open = Gtk.MenuItem(label="Open"); mi_open.connect("activate", lambda *_: self.open_viewer(getattr(widget, "_g_idx", 0)))
        menu.append(mi_open)
        mi_reveal = Gtk.MenuItem(label="Reveal in Folder"); mi_reveal.connect("activate", lambda *_: self._reveal_in_file_manager(path)); menu.append(mi_reveal)
        mi_copy = Gtk.MenuItem(label="Copy Path"); mi_copy.connect("activate", lambda *_: self._copy_path(path)); menu.append(mi_copy)
        mi_delete = Gtk.MenuItem(label="Move to Trash"); mi_delete.connect("activate", lambda *_: self._delete_path(path)); menu.append(mi_delete)
        try:
            menu.show_all()
            try: menu.popup_at_pointer(event)
            except Exception: menu.popup(None, None, None, None, event.button, event.time)
        except Exception: pass
        return True

    def _reveal_in_file_manager(self, path):
        try:
            if sys.platform == "darwin": subprocess.call(["open", "-R", path])
            elif sys.platform.startswith("win"): subprocess.call(["explorer", "/select,", path])
            else: subprocess.call(["xdg-open", os.path.dirname(path)])
        except Exception as e: info_dialog(self.get_toplevel(), "Error", str(e))

    def _copy_path(self, path):
        try:
            clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
            clipboard.set_text(path, -1)
            info_dialog(self.get_toplevel(), "Copied", "Path copied to clipboard")
        except Exception as e: info_dialog(self.get_toplevel(), "Error", str(e))

    def _delete_path(self, path):
        dialog = Gtk.MessageDialog(transient_for=self.get_toplevel(), flags=0,
                                   message_type=Gtk.MessageType.WARNING,
                                   buttons=Gtk.ButtonsType.OK_CANCEL,
                                   text=f"Move to Trash?\n{os.path.basename(path)}")
        resp = dialog.run(); dialog.destroy()
        if resp == Gtk.ResponseType.OK:
            try:
                trash_dir = os.path.expanduser("~/.local/share/Trash/files")
                os.makedirs(trash_dir, exist_ok=True)
                dest = os.path.join(trash_dir, os.path.basename(path))
                i = 0; base = dest
                while os.path.exists(dest):
                    i += 1; dest = f"{base}.{i}"
                shutil.move(path, dest)
                info_dialog(self.get_toplevel(), "Deleted", f"Moved to Trash:\n{dest}")
                if path in self.images:
                    idx = self.images.index(path); del self.images[idx]
                self._populate_thumbs()
                if self._loading_idle:
                    try: GLib.source_remove(self._loading_idle)
                    except Exception: pass
                    self._loading_idle = None
                self._loading_idle = GLib.idle_add(self._fill_thumbnails, 0)
            except Exception as e:
                info_dialog(self.get_toplevel(), "Error", str(e))

    # --- FlowBox activation (Enter/dbl-activate) ---
    def _on_thumb_activated(self, flowbox, child):
        try:
            children = flowbox.get_children()
            if child in children:
                idx = children.index(child)
                self.open_viewer(idx); return True
        except Exception: pass
        return False

    # --- Viewer (iOS-like centered card over translucent backdrop) ---
    def open_viewer(self, index=0):
        if not self.images: return
        self.current_index = max(0, min(index, len(self.images)-1))
        path = self.images[self.current_index]

        parent = self.get_toplevel() if isinstance(self.get_toplevel(), Gtk.Window) else None
        # fullscreen sized transient window used as backdrop
        self.vwin = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        try:
            self.vwin.set_transient_for(parent)
            self.vwin.set_decorated(False)
            self.vwin.set_modal(True)
        except Exception: pass

        # make window cover parent area (or reasonable default)
        try:
            if parent:
                w,h = parent.get_size()
                self.vwin.set_default_size(w, h)
            else:
                self.vwin.set_default_size(1200, 800)
        except Exception:
            self.vwin.set_default_size(1200, 800)

        # backdrop covers whole window and closes viewer when clicked outside card
        backdrop = Gtk.EventBox(); backdrop.get_style_context().add_class("ios-backdrop")
        backdrop.set_visible_window(True)
        backdrop.connect("button-press-event", lambda w, e: self._close_viewer_if_outside(e))
        self.vwin.add(backdrop)

        # center container for card
        center_box = Gtk.Alignment.new(0.5, 0.5, 0, 0)
        backdrop.add(center_box)

        # card
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.get_style_context().add_class("ios-card")
        # allow card to size based on parent but within limits
        try:
            parent_alloc = parent.get_allocation() if parent else None
            if parent_alloc and parent_alloc.width > 0:
                card.set_size_request(min(1000, int(parent_alloc.width*0.86)), min(720, int(parent_alloc.height*0.78)))
            else:
                card.set_size_request(820, 520)
        except Exception:
            try: card.set_size_request(820, 520)
            except Exception: pass

        center_box.add(card)

        # header (title + close)
        hb = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        title = Gtk.Label(label=os.path.basename(path))
        title.set_xalign(0)
        hb.pack_start(title, True, True, 8)
        close_btn = Gtk.Button(); close_btn.get_style_context().add_class("circle")
        try: close_btn.set_image(load_icon("window-close", size=16))
        except Exception: close_btn.set_label("✕")
        close_btn.connect("clicked", lambda *_: self._close_viewer())
        hb.pack_end(close_btn, False, False, 0)
        card.pack_start(hb, False, False, 0)

        # image area (scrolled so image can be big)
        self.img_widget = Gtk.Image(); self.img_widget.get_style_context().add_class("ios-image")
        self.img_event = Gtk.EventBox();
        try: self.img_event.set_visible_window(False); self.img_event.add(self.img_widget)
        except Exception: pass

        scr = Gtk.ScrolledWindow(); scr.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        try: scr.add_with_viewport(self.img_event)
        except Exception:
            try: scr.add(self.img_event)
            except Exception: scr.add_with_viewport(self.img_event)
        card.pack_start(scr, True, True, 0)

        # toolbar with circular buttons and dots indicator
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        toolbar.get_style_context().add_class("ios-toolbar")
        prev_b = Gtk.Button(); prev_b.get_style_context().add_class("circle")
        try: prev_b.set_image(load_icon("go-previous", size=16))
        except Exception: prev_b.set_label("◀")
        prev_b.connect("clicked", lambda *_: self._viewer_prev())
        toolbar.pack_start(prev_b, False, False, 0)

        next_b = Gtk.Button(); next_b.get_style_context().add_class("circle")
        try: next_b.set_image(load_icon("go-next", size=16))
        except Exception: next_b.set_label("▶")
        next_b.connect("clicked", lambda *_: self._viewer_next())
        toolbar.pack_start(next_b, False, False, 0)

        spacer = Gtk.Box(); toolbar.pack_start(spacer, True, True, 0)

        zoom_in = Gtk.Button(); zoom_in.get_style_context().add_class("circle")
        try: zoom_in.set_image(load_icon("zoom-in", size=14))
        except Exception: zoom_in.set_label("+")
        zoom_in.connect("clicked", lambda *_: self._viewer_zoom(1.25))
        toolbar.pack_end(zoom_in, False, False, 0)

        zoom_out = Gtk.Button(); zoom_out.get_style_context().add_class("circle")
        try: zoom_out.set_image(load_icon("zoom-out", size=14))
        except Exception: zoom_out.set_label("-")
        zoom_out.connect("clicked", lambda *_: self._viewer_zoom(0.8))
        toolbar.pack_end(zoom_out, False, False, 0)

        rotate = Gtk.Button(); rotate.get_style_context().add_class("circle")
        try: rotate.set_image(load_icon("object-rotate-right", size=14))
        except Exception: rotate.set_label("⤾")
        rotate.connect("clicked", lambda *_: self._viewer_rotate(90))
        toolbar.pack_end(rotate, False, False, 0)

        card.pack_start(toolbar, False, False, 0)

        # dots indicator
        self._dots_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self._rebuild_dots()
        card.pack_start(self._dots_box, False, False, 0)

        # events
        try: self.vwin.connect("key-press-event", self._viewer_on_key)
        except Exception: pass
        try: self.img_event.connect("scroll-event", self._viewer_on_scroll)
        except Exception: pass
        try: self.vwin.connect("destroy", lambda *_: self._stop_slideshow_if_needed())
        except Exception: pass

        # reset viewer state
        self._viewer_scale = 1.0
        self._viewer_rotation = 0
        self._viewer_pixbuf_original = None

        # show and then load image on idle (so UI draws first)
        try:
            self.vwin.show_all()
            self.vwin.present()
        except Exception: pass
        GLib.idle_add(self._viewer_load_image)

    def _close_viewer_if_outside(self, event):
        # close if click on backdrop (outside card)
        # event coordinates are window-relative; we just close always on backdrop click
        self._close_viewer()
        return True

    def _close_viewer(self):
        try:
            if getattr(self, "vwin", None):
                self.vwin.destroy()
                self.vwin = None
        except Exception: pass

    def _rebuild_dots(self):
        try:
            self._dots_box.foreach(lambda w: self._dots_box.remove(w))
        except Exception:
            for w in list(self._dots_box.get_children()):
                try: self._dots_box.remove(w)
                except Exception: pass
        total = len(self.images)
        if total <= 1: return
        for i in range(total):
            d = Gtk.EventBox()
            d.get_style_context().add_class("dot")
            if i == self.current_index:
                d.get_style_context().add_class("active")
            self._dots_box.pack_start(d, False, False, 0)
        try: self._dots_box.show_all()
        except Exception: pass

    # --- viewer load / rendering ---
    def _viewer_load_image(self):
        if not getattr(self, "vwin", None) or not getattr(self, "img_widget", None):
            return False
        path = self.images[self.current_index]
        try:
            # target area estimation
            alloc_w = alloc_h = 0
            try:
                alloc = self.img_event.get_allocation()
                alloc_w, alloc_h = alloc.width, alloc.height
            except Exception:
                pass
            if alloc_w <= 1 or alloc_h <= 1:
                try:
                    w,h = self.vwin.get_size()
                    alloc_w, alloc_h = max(640, w - 160), max(360, h - 200)
                except Exception:
                    alloc_w, alloc_h = 1000, 700

            # load scaled
            try:
                pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, alloc_w, alloc_h, True)
            except TypeError:
                pb = GdkPixbuf.Pixbuf.new_from_file(path)
                w, h = pb.get_width(), pb.get_height()
                scale = min(1.0, float(max(alloc_w, alloc_h)) / max(w, h))
                if scale < 1.0:
                    pb = pb.scale_simple(int(w*scale), int(h*scale), GdkPixbuf.InterpType.BILINEAR)
            except Exception:
                pb = GdkPixbuf.Pixbuf.new_from_file(path)

            self._viewer_pixbuf_original = pb

            # rotation
            try:
                pb_final = pb
                if self._viewer_rotation % 360 != 0:
                    if self._viewer_rotation % 360 == 90:
                        pb_final = pb.rotate_simple(GdkPixbuf.PixbufRotation.ROTATE_90)
                    elif self._viewer_rotation % 360 == 180:
                        pb_final = pb.rotate_simple(GdkPixbuf.PixbufRotation.ROTATE_180)
                    elif self._viewer_rotation % 360 == 270:
                        pb_final = pb.rotate_simple(GdkPixbuf.PixbufRotation.ROTATE_270)
                else:
                    pb_final = pb
            except Exception:
                pb_final = pb

            # scale for viewer (zoom)
            if self._viewer_scale != 1.0:
                try:
                    w,h = pb_final.get_width(), pb_final.get_height()
                    nw, nh = max(1, int(w * self._viewer_scale)), max(1, int(h * self._viewer_scale))
                    pb_final = pb_final.scale_simple(nw, nh, GdkPixbuf.InterpType.BILINEAR)
                except Exception:
                    pass

            try:
                self.img_widget.set_from_pixbuf(pb_final)
            except Exception:
                try: self.img_widget.set_from_icon_name("image-missing", Gtk.IconSize.DIALOG)
                except Exception: pass

            # update dots
            try:
                self._rebuild_dots()
            except Exception: pass

        except Exception as e:
            try: self.img_widget.set_from_icon_name("image-missing", Gtk.IconSize.DIALOG)
            except Exception: pass
            info_dialog(self.get_toplevel(), "Error", f"Could not load image:\n{e}")

        return False

    # --- viewer controls (prev/next/zoom/rotate) ---
    def _viewer_prev(self):
        if not self.images: return
        self.current_index = max(0, self.current_index - 1)
        self._viewer_scale = 1.0; self._viewer_rotation = 0
        self._viewer_load_image()

    def _viewer_next(self):
        if not self.images: return
        self.current_index = min(len(self.images)-1, self.current_index + 1)
        self._viewer_scale = 1.0; self._viewer_rotation = 0
        self._viewer_load_image()

    def _viewer_zoom(self, factor):
        if not getattr(self, "vwin", None): return
        self._viewer_scale = max(0.05, min(10.0, self._viewer_scale * factor))
        if self._viewer_pixbuf_original is not None:
            pb = self._viewer_pixbuf_original
            try:
                # apply rotation
                if self._viewer_rotation % 360 != 0:
                    if self._viewer_rotation % 360 == 90:
                        pb = pb.rotate_simple(GdkPixbuf.PixbufRotation.ROTATE_90)
                    elif self._viewer_rotation % 360 == 180:
                        pb = pb.rotate_simple(GdkPixbuf.PixbufRotation.ROTATE_180)
                    elif self._viewer_rotation % 360 == 270:
                        pb = pb.rotate_simple(GdkPixbuf.PixbufRotation.ROTATE_270)
                w,h = pb.get_width(), pb.get_height()
                nw, nh = max(1, int(w * self._viewer_scale)), max(1, int(h * self._viewer_scale))
                pb2 = pb.scale_simple(nw, nh, GdkPixbuf.InterpType.BILINEAR)
                self.img_widget.set_from_pixbuf(pb2)
            except Exception:
                self._viewer_load_image()
        else:
            self._viewer_load_image()

    def _viewer_rotate(self, deg):
        if not getattr(self, "vwin", None): return
        self._viewer_rotation = (self._viewer_rotation + int(deg)) % 360
        if self._viewer_pixbuf_original is not None:
            try:
                pb = self._viewer_pixbuf_original
                if self._viewer_rotation % 360 == 90:
                    pb = pb.rotate_simple(GdkPixbuf.PixbufRotation.ROTATE_90)
                elif self._viewer_rotation % 360 == 180:
                    pb = pb.rotate_simple(GdkPixbuf.PixbufRotation.ROTATE_180)
                elif self._viewer_rotation % 360 == 270:
                    pb = pb.rotate_simple(GdkPixbuf.PixbufRotation.ROTATE_270)
                if self._viewer_scale != 1.0:
                    w,h = pb.get_width(), pb.get_height()
                    nw, nh = max(1, int(w * self._viewer_scale)), max(1, int(h * self._viewer_scale))
                    pb = pb.scale_simple(nw, nh, GdkPixbuf.InterpType.BILINEAR)
                self.img_widget.set_from_pixbuf(pb)
            except Exception:
                self._viewer_load_image()
        else:
            self._viewer_load_image()

    def _viewer_on_key(self, win, event):
        try: key = Gdk.keyval_name(event.keyval)
        except Exception: return False
        if key in ("Left", "KP_Left"): self._viewer_prev(); return True
        if key in ("Right", "KP_Right"): self._viewer_next(); return True
        if key in ("Escape",): self._close_viewer(); return True
        if key in ("plus", "KP_Add"): self._viewer_zoom(1.25); return True
        if key in ("minus", "KP_Subtract"): self._viewer_zoom(0.8); return True
        return False

    def _viewer_on_scroll(self, widget, event):
        try: direction = event.direction
        except Exception: return False
        try: ctrl = bool(event.state & Gdk.ModifierType.CONTROL_MASK)
        except Exception: ctrl = False
        if ctrl:
            if direction == Gdk.ScrollDirection.UP: self._viewer_rotate(90)
            else: self._viewer_rotate(-90)
        else:
            if direction == Gdk.ScrollDirection.UP: self._viewer_zoom(1.06)
            else: self._viewer_zoom(0.94)
        return True

    # slideshow
    def _toggle_slideshow(self, *_):
        if self._slideshow_id is None:
            self._slideshow_id = GLib.timeout_add_seconds(3, self._slideshow_tick)
            info_dialog(self.get_toplevel(), "Slideshow", "Started")
        else:
            self._stop_slideshow_if_needed()
            info_dialog(self.get_toplevel(), "Slideshow", "Stopped")

    def _slideshow_tick(self):
        if not self.images: return False
        self.current_index = (self.current_index + 1) % len(self.images)
        if getattr(self, "vwin", None) and self.vwin.get_visible():
            self._viewer_load_image()
        return True

    def _stop_slideshow_if_needed(self):
        if self._slideshow_id is not None:
            try: GLib.source_remove(self._slideshow_id)
            except Exception: pass
            self._slideshow_id = None

    def _viewer_action_or_hint(self, fn, *args, **kwargs):
        if getattr(self, "vwin", None) and getattr(self, "vwin", "visible") and getattr(self.vwin, "get_visible", lambda: False)():
            try: fn(*args, **kwargs)
            except Exception: pass
        else:
            info_dialog(self.get_toplevel(), "Info", "Open an image first to use this control.")
