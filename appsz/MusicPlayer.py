# ---------- MusicPlayer ----------
# Modern MusicPlayer (GStreamer-backed with graceful fallback)
# ---------- MusicPlayer (full) ----------
# Real GStreamer player + lightweight audio editor (trim/join/convert/tags)
# - Playback: GStreamer playbin
# - Editing: pydub (FFmpeg) — graceful fallback if not installed
# - Tags/Cover: mutagen — graceful fallback
# - DnD to playlist, add folder, hotkeys, cover art preview

import os
import io
import sys
import random
import threading
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib, GdkPixbuf

# try GStreamer
HAVE_GST = False
try:
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst
    Gst.init(None)
    HAVE_GST = True
except Exception:
    HAVE_GST = False

# optional editing libs
HAVE_PYDUB = False
HAVE_MUTAGEN = False
try:
    from pydub import AudioSegment
    HAVE_PYDUB = True
except Exception:
    HAVE_PYDUB = False

try:
    import mutagen
    from mutagen.id3 import ID3, APIC, TIT2, TPE1, TALB
    from mutagen.flac import Picture
    HAVE_MUTAGEN = True
except Exception:
    HAVE_MUTAGEN = False

from helpers import info_dialog, load_icon

AUDIO_EXTS = (".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac", ".opus")

# ---------------- small utils ----------------
def _human_time(sec: int) -> str:
    m = sec // 60
    s = sec % 60
    return f"{m:02d}:{s:02d}"

def _guess_fmt(path: str) -> str:
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    return ext or "mp3"

# ---------------- Editor Dialogs ----------------
#!/usr/bin/env python3
"""
Full MusicPlayer widget (GTK3) with:
 - GStreamer playback (if available)
 - pydub + simpleaudio fallback playback (if GStreamer missing)
 - trim/convert/join using pydub (requires ffmpeg)
 - tags & cover edit using mutagen (optional)
 - drag & drop, add-folder, join, convert, tags dialogs

Drop this file into your project and import MusicPlayer, or run directly to test.

Dependencies (recommended):
  - GStreamer + plugins (for macOS via brew: brew install gstreamer gst-plugins-base gst-plugins-good gst-plugins-bad gst-plugins-ugly)
  - pygobject (pip install pygobject)
  - pydub (pip install pydub)
  - simpleaudio (pip install simpleaudio)
  - mutagen (pip install mutagen)
  - ffmpeg installed on PATH (for pydub export)

This file implements a reasonably full-featured but lightweight audio player/editor.
"""

import os
import sys
import threading
import random
import io

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib, GdkPixbuf

# Try GStreamer
HAVE_GST = False
try:
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst
    Gst.init(None)
    HAVE_GST = True
except Exception:
    HAVE_GST = False

# Optional editing libs
HAVE_PYDUB = False
HAVE_SIMPLEAUDIO = False
HAVE_MUTAGEN = False
try:
    from pydub import AudioSegment
    HAVE_PYDUB = True
except Exception:
    HAVE_PYDUB = False

try:
    import simpleaudio
    HAVE_SIMPLEAUDIO = True
except Exception:
    HAVE_SIMPLEAUDIO = False

try:
    import mutagen
    from mutagen.id3 import ID3, APIC, TIT2, TPE1, TALB
    from mutagen.flac import Picture
    HAVE_MUTAGEN = True
except Exception:
    HAVE_MUTAGEN = False

# Small helpers -------------------------------------------------
AUDIO_EXTS = (".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac", ".opus")

def info_dialog(parent, title, text):
    dlg = Gtk.MessageDialog(transient_for=parent if isinstance(parent, Gtk.Window) else None,
                            flags=0, message_type=Gtk.MessageType.INFO,
                            buttons=Gtk.ButtonsType.OK, text=title)
    dlg.format_secondary_text(str(text))
    dlg.run(); dlg.destroy()

def load_icon(name_or_path, size=24):
    # Try to treat as path first
    try:
        if os.path.exists(name_or_path):
            pb = GdkPixbuf.Pixbuf.new_from_file_at_size(name_or_path, size, size)
            img = Gtk.Image.new_from_pixbuf(pb)
            return img
    except Exception:
        pass
    # fallback to themed icon name
    try:
        return Gtk.Image.new_from_icon_name(name_or_path, Gtk.IconSize.DIALOG)
    except Exception:
        return Gtk.Image()

def _human_time(sec: int) -> str:
    m = sec // 60; s = sec % 60
    return f"{m:02d}:{s:02d}"

def _guess_fmt(path: str) -> str:
    ext = os.path.splitext(path)[1].lower().lstrip('.')
    return ext or 'mp3'

# ---------------- Dialogs (Trim / Convert / Tags) ----------------
class AudioTrimDialog(Gtk.Dialog):
    def __init__(self, parent, src_path: str):
        super().__init__(title="Trim / Cut", transient_for=parent, flags=0)
        self.add_button(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL)
        self.add_button("Export", Gtk.ResponseType.OK)
        self.set_default_size(480, 220)
        self.src_path = src_path

        box = self.get_content_area()
        grid = Gtk.Grid(column_spacing=8, row_spacing=8, margin=12)
        box.add(grid)

        grid.attach(Gtk.Label(label=f"Source: {os.path.basename(src_path)}"), 0, 0, 2, 1)

        grid.attach(Gtk.Label(label="Start (sec)"), 0, 1, 1, 1)
        self.start_adj = Gtk.Adjustment(0, 0, 10_000, 1, 5)
        start_entry = Gtk.SpinButton(adjustment=self.start_adj, digits=0)
        grid.attach(start_entry, 1, 1, 1, 1)

        grid.attach(Gtk.Label(label="End (sec)"), 0, 2, 1, 1)
        self.end_adj = Gtk.Adjustment(0, 0, 10_000, 1, 5)
        end_entry = Gtk.SpinButton(adjustment=self.end_adj, digits=0)
        grid.attach(end_entry, 1, 2, 1, 1)

        grid.attach(Gtk.Label(label="Export as"), 0, 3, 1, 1)
        self.format_combo = Gtk.ComboBoxText()
        for f in ("mp3", "wav", "ogg", "flac", "m4a"):
            self.format_combo.append_text(f)
        self.format_combo.set_active(0)
        grid.attach(self.format_combo, 1, 3, 1, 1)

        self.show_all()

    def get_values(self):
        return int(self.start_adj.get_value()), int(self.end_adj.get_value()), self.format_combo.get_active_text()

class ConvertDialog(Gtk.Dialog):
    def __init__(self, parent, src_path: str):
        super().__init__(title="Convert Audio", transient_for=parent, flags=0)
        self.add_button(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL)
        self.add_button("Convert", Gtk.ResponseType.OK)
        self.set_default_size(440, 160)
        self.src_path = src_path

        box = self.get_content_area()
        grid = Gtk.Grid(column_spacing=8, row_spacing=8, margin=12)
        box.add(grid)

        grid.attach(Gtk.Label(label=f"Source: {os.path.basename(src_path)}"), 0, 0, 2, 1)

        grid.attach(Gtk.Label(label="Target format"), 0, 1, 1, 1)
        self.format_combo = Gtk.ComboBoxText()
        for f in ("mp3", "wav", "ogg", "flac", "m4a"):
            self.format_combo.append_text(f)
        self.format_combo.set_active(0)
        grid.attach(self.format_combo, 1, 1, 1, 1)

        grid.attach(Gtk.Label(label="Bitrate (kbps, mp3/m4a)"), 0, 2, 1, 1)
        self.br_entry = Gtk.Entry()
        self.br_entry.set_text("192")
        grid.attach(self.br_entry, 1, 2, 1, 1)

        self.show_all()

    def get_values(self):
        fmt = self.format_combo.get_active_text()
        br = self.br_entry.get_text().strip() or "192"
        return fmt, br

class TagEditorDialog(Gtk.Dialog):
    def __init__(self, parent, src_path: str, tags: dict):
        super().__init__(title="Edit Tags", transient_for=parent, flags=0)
        self.add_button(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL)
        self.add_button("Save", Gtk.ResponseType.OK)
        self.set_default_size(480, 260)
        self.src_path = src_path

        box = self.get_content_area()
        grid = Gtk.Grid(column_spacing=8, row_spacing=8, margin=12)
        box.add(grid)

        self.e_title = Gtk.Entry(); self.e_artist = Gtk.Entry(); self.e_album = Gtk.Entry()
        self.e_title.set_text(tags.get("title", ""))
        self.e_artist.set_text(tags.get("artist", ""))
        self.e_album.set_text(tags.get("album", ""))

        grid.attach(Gtk.Label(label="Title"), 0, 0, 1, 1); grid.attach(self.e_title, 1, 0, 1, 1)
        grid.attach(Gtk.Label(label="Artist"), 0, 1, 1, 1); grid.attach(self.e_artist, 1, 1, 1, 1)
        grid.attach(Gtk.Label(label="Album"), 0, 2, 1, 1); grid.attach(self.e_album, 1, 2, 1, 1)

        self.cover_btn = Gtk.Button(label="Load Cover…")
        grid.attach(self.cover_btn, 0, 3, 2, 1)

        self.cover_path = None
        self.cover_btn.connect("clicked", self.on_pick_cover)

        self.show_all()

    def on_pick_cover(self, *_):
        dlg = Gtk.FileChooserDialog(title="Pick cover image", parent=self, action=Gtk.FileChooserAction.OPEN)
        dlg.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OPEN, Gtk.ResponseType.OK)
        flt = Gtk.FileFilter(); flt.set_name("Images")
        for p in ("*.png", "*.jpg", "*.jpeg", "*.webp"):
            flt.add_pattern(p)
        dlg.add_filter(flt)
        if dlg.run() == Gtk.ResponseType.OK:
            self.cover_path = dlg.get_filename()
        dlg.destroy()

    def get_values(self):
        return {
            "title": self.e_title.get_text().strip(),
            "artist": self.e_artist.get_text().strip(),
            "album": self.e_album.get_text().strip(),
            "cover": self.cover_path,
        }

# ---------------- MusicPlayer ----------------
class MusicPlayer(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.set_border_width(8)
        self.set_can_focus(True)
        self.add_events(Gdk.EventMask.KEY_PRESS_MASK | Gdk.EventMask.KEY_RELEASE_MASK)

        # state
        self.playlist = []
        self.current_index = -1
        self.playing = False
        self.loop = False
        self.shuffle = False
        self._seeking = False
        self._duration = Gst.CLOCK_TIME_NONE if HAVE_GST else 0

        # fallback playback state (pydub+simpleaudio)
        self._sa_play_obj = None
        self._sa_segment = None
        self._sa_pos_ms = 0  # current offset when paused/stopped

        # player
        if HAVE_GST:
            self.player = Gst.ElementFactory.make("playbin", "player")
            bus = self.player.get_bus()
            bus.add_signal_watch(); bus.connect("message", self._on_bus_message)
        else:
            self.player = None

        # header controls
        hb_top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        lbl = Gtk.Label(label="Playlist"); lbl.set_xalign(0)
        hb_top.pack_start(lbl, True, True, 0)

        add_btn = Gtk.Button(); add_btn.set_relief(Gtk.ReliefStyle.NONE)
        try: add_btn.set_image(load_icon("list-add", size=14))
        except Exception: add_btn.set_label("+")
        add_btn.set_tooltip_text("Add files…")
        add_btn.connect("clicked", self.on_add)
        hb_top.pack_start(add_btn, False, False, 0)

        add_dir_btn = Gtk.Button(); add_dir_btn.set_relief(Gtk.ReliefStyle.NONE)
        try: add_dir_btn.set_image(load_icon("folder-new", size=14))
        except Exception: add_dir_btn.set_label("📁+")
        add_dir_btn.set_tooltip_text("Add folder…")
        add_dir_btn.connect("clicked", self.on_add_folder)
        hb_top.pack_start(add_dir_btn, False, False, 0)

        edit_btn = Gtk.Button(label="Edit"); edit_btn.connect("clicked", self.on_edit)
        hb_top.pack_start(edit_btn, False, False, 0)

        conv_btn = Gtk.Button(label="Convert"); conv_btn.connect("clicked", self.on_convert)
        hb_top.pack_start(conv_btn, False, False, 0)

        tag_btn = Gtk.Button(label="Tags"); tag_btn.connect("clicked", self.on_tags)
        hb_top.pack_start(tag_btn, False, False, 0)

        join_btn = Gtk.Button(label="Join…"); join_btn.connect("clicked", self.on_join)
        hb_top.pack_start(join_btn, False, False, 0)

        self.pack_start(hb_top, False, False, 0)

        # list
        self.listbox = Gtk.ListBox(); self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.listbox.connect("row-activated", self._on_row_activated)
        sc = Gtk.ScrolledWindow(); sc.set_min_content_height(220); sc.add(self.listbox)
        self.pack_start(sc, True, True, 0)

        # drag target
        self.drag_dest_set(Gtk.DestDefaults.ALL, [], Gdk.DragAction.COPY)
        targets = Gtk.TargetEntry.new("text/uri-list", 0, 0)
        self.drag_dest_set_target_list(Gtk.TargetList.new([targets]))
        self.connect("drag-data-received", self._on_drag_data_received)

        # cover + controls
        cover_and_ctrl = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.cover_img = Gtk.Image(); self.cover_img.set_from_icon_name("audio-x-generic", Gtk.IconSize.DIALOG)
        cover_frame = Gtk.Frame(); cover_frame.set_shadow_type(Gtk.ShadowType.IN); cover_frame.add(self.cover_img)
        cover_and_ctrl.pack_start(cover_frame, False, False, 0)

        ctrl_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        ctrl = Gtk.Box(spacing=8); ctrl.set_halign(Gtk.Align.CENTER)

        prev_btn = Gtk.Button(); prev_btn.set_relief(Gtk.ReliefStyle.NONE)
        try: prev_btn.set_image(load_icon("go-previous", size=16))
        except Exception: prev_btn.set_label("⏮")
        prev_btn.connect("clicked", lambda *_: self.prev())
        ctrl.pack_start(prev_btn, False, False, 0)

        self.play_btn = Gtk.Button(); self.play_btn.set_relief(Gtk.ReliefStyle.NONE)
        try: self.play_btn.set_image(load_icon("media-playback-start", size=16))
        except Exception: self.play_btn.set_label("Play")
        self.play_btn.connect("clicked", lambda *_: self.toggle_play())
        ctrl.pack_start(self.play_btn, False, False, 0)

        stop_btn = Gtk.Button(); stop_btn.set_relief(Gtk.ReliefStyle.NONE)
        try: stop_btn.set_image(load_icon("media-playback-stop", size=16))
        except Exception: stop_btn.set_label("Stop")
        stop_btn.connect("clicked", self.stop)
        ctrl.pack_start(stop_btn, False, False, 0)

        next_btn = Gtk.Button(); next_btn.set_relief(Gtk.ReliefStyle.NONE)
        try: next_btn.set_image(load_icon("go-next", size=16))
        except Exception: next_btn.set_label("⏭")
        next_btn.connect("clicked", lambda *_: self.next())
        ctrl.pack_start(next_btn, False, False, 0)

        # shuffle / loop
        self.shuffle_btn = Gtk.ToggleButton(); self.shuffle_btn.connect("toggled", lambda b: setattr(self, 'shuffle', b.get_active()))
        try: self.shuffle_btn.set_image(load_icon("media-playlist-shuffle", size=14))
        except Exception: self.shuffle_btn.set_label("S")
        ctrl.pack_start(self.shuffle_btn, False, False, 0)

        self.loop_btn = Gtk.ToggleButton(); self.loop_btn.connect("toggled", lambda b: setattr(self, 'loop', b.get_active()))
        try: self.loop_btn.set_image(load_icon("media-playlist-repeat", size=14))
        except Exception: self.loop_btn.set_label("R")
        ctrl.pack_start(self.loop_btn, False, False, 0)

        # progress + volume
        pv = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.position_scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL); self.position_scale.set_draw_value(False)
        self.position_scale.set_range(0, 1000); self.position_scale.set_sensitive(HAVE_GST)
        self.position_scale.connect("button-press-event", lambda *_: self._start_seek())
        self.position_scale.connect("button-release-event", lambda *_: self._end_seek())
        pv.pack_start(self.position_scale, True, True, 0)

        self.time_lbl = Gtk.Label(label="00:00 / 00:00")
        pv.pack_start(self.time_lbl, False, False, 0)

        vol_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6); vol_box.pack_start(Gtk.Label(label="Vol"), False, False, 0)
        self.vol_scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL); self.vol_scale.set_range(0,1); self.vol_scale.set_value(0.8)
        self.vol_scale.set_draw_value(False); self.vol_scale.set_size_request(120, -1)
        self.vol_scale.connect("value-changed", self._on_volume_changed)
        vol_box.pack_start(self.vol_scale, False, False, 0)

        ctrl_col.pack_start(ctrl, False, False, 0); ctrl_col.pack_start(pv, False, False, 0); ctrl_col.pack_start(vol_box, False, False, 0)
        cover_and_ctrl.pack_start(ctrl_col, True, True, 0)
        self.pack_start(cover_and_ctrl, False, False, 0)

        self.status_lbl = Gtk.Label(label="Stopped"); self.pack_start(self.status_lbl, False, False, 0)

        if HAVE_GST:
            GLib.timeout_add(500, self._update_position)
        else:
            GLib.timeout_add(500, self._update_position)

        self.connect("key-press-event", self._on_key)

        # preload ~/Music
        music_dir = os.path.expanduser("~/Music")
        if os.path.isdir(music_dir):
            initial = [os.path.join(music_dir, f) for f in sorted(os.listdir(music_dir)) if f.lower().endswith(AUDIO_EXTS)]
            self.add_tracks(initial)

    # playlist helpers
    def _add_one(self, p: str):
        if p and os.path.isfile(p) and p.lower().endswith(AUDIO_EXTS):
            self.playlist.append(p)
            row = Gtk.ListBoxRow(); box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            lbl = Gtk.Label(label=os.path.basename(p)); lbl.set_xalign(0); box.pack_start(lbl, True, True, 0)
            row.add(box); row._path = p; self.listbox.add(row)

    def add_tracks(self, paths):
        for p in paths: self._add_one(p)
        self.listbox.show_all()

    def on_add(self, *_):
        dlg = Gtk.FileChooserDialog(title="Add audio files", parent=self.get_toplevel(), action=Gtk.FileChooserAction.OPEN)
        dlg.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, "Add", Gtk.ResponseType.OK)
        dlg.set_select_multiple(True)
        flt = Gtk.FileFilter(); flt.set_name("Audio")
        for ext in AUDIO_EXTS: flt.add_pattern(f"*{ext}")
        dlg.add_filter(flt)
        files = []
        if dlg.run() == Gtk.ResponseType.OK: files = dlg.get_filenames()
        dlg.destroy(); self.add_tracks(files)

    def on_add_folder(self, *_):
        dlg = Gtk.FileChooserDialog(title="Add folder", parent=self.get_toplevel(), action=Gtk.FileChooserAction.SELECT_FOLDER)
        dlg.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, "Add", Gtk.ResponseType.OK)
        paths = []
        if dlg.run() == Gtk.ResponseType.OK:
            base = dlg.get_filename(); dlg.destroy()
            for root, _, files in os.walk(base):
                for f in files:
                    if f.lower().endswith(AUDIO_EXTS): paths.append(os.path.join(root, f))
        else:
            dlg.destroy(); return
        self.add_tracks(sorted(paths))

    def _on_drag_data_received(self, widget, drag_context, x, y, data, info, time):
        try:
            uris = data.get_uris(); paths = []
            for u in uris:
                p = Gdk.uri_unescape_string(u.replace("file://", ""))
                if os.path.isdir(p):
                    for root, _, files in os.walk(p):
                        for f in files:
                            if f.lower().endswith(AUDIO_EXTS): paths.append(os.path.join(root, f))
                else:
                    paths.append(p)
            self.add_tracks(paths)
        except Exception:
            pass

    def clear_playlist(self):
        self.playlist = []
        for c in list(self.listbox.get_children()): self.listbox.remove(c)
        self.current_index = -1; self.stop()

    def _on_row_activated(self, listbox, row):
        path = getattr(row, "_path", None)
        if path and path in self.playlist:
            idx = self.playlist.index(path); self.play_index(idx)

    # playback control
    def play_index(self, idx):
        if idx < 0 or idx >= len(self.playlist): return
        self.current_index = idx; path = self.playlist[idx]
        if HAVE_GST and self.player:
            uri = Gst.filename_to_uri(path); self.player.set_property("uri", uri); self.player.set_state(Gst.State.PLAYING)
            self.playing = True; self.play_btn.set_tooltip_text("Pause"); self.status_lbl.set_text(f"Playing: {os.path.basename(path)}")
            try: self.player.set_property("volume", float(self.vol_scale.get_value()))
            except Exception: pass
            self._load_cover_art(path)
        else:
            # fallback using pydub + simpleaudio
            if not HAVE_PYDUB:
                info_dialog(self.get_toplevel(), "Playback", "No playback backend available (install GStreamer or pydub+simpleaudio)")
                return
            try:
                seg = AudioSegment.from_file(path)
                self._sa_segment = seg
                self._sa_pos_ms = 0
                self._sa_play(seg[self._sa_pos_ms:])
                self.playing = True
                self.play_btn.set_label("Pause")
                self.status_lbl.set_text(f"Playing (fallback): {os.path.basename(path)}")
                self._load_cover_art(path)
            except Exception as e:
                info_dialog(self.get_toplevel(), "Playback error", str(e))

    def toggle_play(self):
        if HAVE_GST and self.player:
            state = self.player.get_state(0).state
            if state == Gst.State.PLAYING:
                self.player.set_state(Gst.State.PAUSED); self.playing = False; self.play_btn.set_tooltip_text("Play"); self.status_lbl.set_text("Paused")
            else:
                if self.current_index < 0 and self.playlist: self.play_index(0)
                else: self.player.set_state(Gst.State.PLAYING); self.playing = True; self.play_btn.set_tooltip_text("Pause")
        else:
            # simpleaudio fallback: pause/resume via slicing
            if not HAVE_PYDUB:
                return
            if self.playing:
                # stop and record position
                if self._sa_play_obj:
                    try: self._sa_pos_ms += int(self._sa_play_obj.time * 1000)
                    except Exception: pass
                    try: self._sa_play_obj.stop()
                    except Exception: pass
                self.playing = False; self.play_btn.set_label("Play"); self.status_lbl.set_text("Paused (fallback)")
            else:
                # resume from _sa_pos_ms
                if self._sa_segment is None and self.current_index >= 0:
                    self.play_index(self.current_index); return
                if self._sa_segment is None:
                    return
                seg = self._sa_segment[self._sa_pos_ms:]
                self._sa_play(seg)
                self.playing = True; self.play_btn.set_label("Pause"); self.status_lbl.set_text("Playing (fallback)")

    def stop(self, *_):
        if HAVE_GST and self.player:
            self.player.set_state(Gst.State.NULL)
        else:
            if self._sa_play_obj:
                try: self._sa_play_obj.stop()
                except Exception: pass
            self._sa_play_obj = None; self._sa_segment = None; self._sa_pos_ms = 0
        self.playing = False; self.play_btn.set_tooltip_text("Play"); self.play_btn.set_label("Play")
        self.status_lbl.set_text("Stopped"); self._duration = Gst.CLOCK_TIME_NONE if HAVE_GST else 0
        try: self.position_scale.set_value(0)
        except Exception: pass

    def next(self):
        if not self.playlist: return
        if self.shuffle: self.current_index = random.randrange(len(self.playlist))
        else: self.current_index = (self.current_index + 1) % len(self.playlist)
        self.play_index(self.current_index)

    def prev(self):
        if not self.playlist: return
        if self.shuffle: self.current_index = random.randrange(len(self.playlist))
        else: self.current_index = (self.current_index - 1) % len(self.playlist)
        self.play_index(self.current_index)

    # volume / seek
    def _on_volume_changed(self, scale):
        v = float(scale.get_value())
        if HAVE_GST and self.player:
            try: self.player.set_property("volume", v)
            except Exception: pass
        # simpleaudio has no global volume control — could implement via re-exporting segment

    def _update_position(self):
        # update UI position/time for both backends
        try:
            if HAVE_GST and self.player:
                success, position = self.player.query_position(Gst.Format.TIME)
                success2, duration = self.player.query_duration(Gst.Format.TIME)
                if success and success2:
                    self._duration = duration
                    pos_s = int(position / Gst.SECOND)
                    dur_s = int(duration / Gst.SECOND) if duration > 0 else 0
                    self.time_lbl.set_text(f"{_human_time(pos_s)} / {_human_time(dur_s)}")
                    if duration > 0 and not self._seeking:
                        fraction = float(position) / float(duration)
                        self.position_scale.set_range(0, 1000); self.position_scale.set_value(fraction * 1000.0)
            else:
                if self._sa_segment is not None and self._sa_play_obj is not None:
                    # simpleaudio exposes .is_playing() and .time available via private attr sometimes
                    try:
                        is_playing = self._sa_play_obj.is_playing()
                        elapsed_ms = int(self._sa_play_obj.time * 1000) if hasattr(self._sa_play_obj, 'time') else 0
                    except Exception:
                        is_playing = False; elapsed_ms = 0
                    cur_ms = self._sa_pos_ms + elapsed_ms
                    dur_ms = len(self._sa_segment)
                    self.time_lbl.set_text(f"{_human_time(cur_ms//1000)} / {_human_time(dur_ms//1000)}")
                else:
                    # nothing playing
                    pass
        except Exception:
            pass
        return True

    def _start_seek(self): self._seeking = True
    def _end_seek(self):
        if not (HAVE_GST and self.player):
            self._seeking = False; return
        try:
            value = self.position_scale.get_value(); fraction = float(value)/1000.0
            if self._duration and self._duration > 0:
                newpos = int(fraction * float(self._duration))
                self.player.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT, newpos)
        except Exception:
            pass
        self._seeking = False

    def _on_seek_value_changed(self, scale):
        if not self._seeking: return
        if self._duration and self._duration > 0:
            fraction = float(scale.get_value())/1000.0
            pos = int(fraction * float(self._duration))
            self.time_lbl.set_text(f"{_human_time(int(pos / Gst.SECOND))} / {_human_time(int(self._duration / Gst.SECOND))}")

    def _on_bus_message(self, bus, msg):
        t = msg.type
        try:
            if t == Gst.MessageType.EOS:
                if self.loop: self.play_index(self.current_index)
                else:
                    if self.current_index + 1 < len(self.playlist): self.next()
                    else: self.stop()
            elif t == Gst.MessageType.ERROR:
                err, debug = msg.parse_error(); info_dialog(self.get_toplevel(), "Playback error", str(err)); self.stop()
        except Exception:
            pass

    # keybinds
    def _on_key(self, widget, ev):
        name = Gdk.keyval_name(ev.keyval)
        if name in ("space", "Space"): self.toggle_play(); return True
        if name in ("Left", "KP_Left"): self._nudge(-5); return True
        if name in ("Right", "KP_Right"): self._nudge(5); return True
        if name in ("Up", "KP_Up"): self.vol_scale.set_value(min(1.0, self.vol_scale.get_value()+0.05)); return True
        if name in ("Down", "KP_Down"): self.vol_scale.set_value(max(0.0, self.vol_scale.get_value()-0.05)); return True
        return False

    def _nudge(self, seconds):
        if HAVE_GST and self.player:
            try:
                success, position = self.player.query_position(Gst.Format.TIME)
                if success:
                    newpos = max(0, int(position + seconds*Gst.SECOND))
                    self.player.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT, newpos)
            except Exception:
                pass
        else:
            # fallback: approximate via stopping and restarting at new offset
            if not HAVE_PYDUB: return
            if self._sa_segment is None: return
            cur = self._sa_pos_ms
            # try to get elapsed
            try:
                elapsed = int(self._sa_play_obj.time*1000) if (self._sa_play_obj and hasattr(self._sa_play_obj, 'time')) else 0
            except Exception:
                elapsed = 0
            new_ms = max(0, cur + elapsed + seconds*1000)
            if new_ms >= len(self._sa_segment):
                self.stop(); return
            # restart from new_ms
            if self._sa_play_obj:
                try: self._sa_play_obj.stop()
                except Exception: pass
            self._sa_pos_ms = new_ms
            self._sa_play(self._sa_segment[self._sa_pos_ms:])

    # cover art via mutagen
    def _load_cover_art(self, path: str, max_size: int = 120):
        """
        Load cover art (from file or from tags) and show it scaled to max_size x max_size
        - path: audio file path
        - max_size: maximum pixel size for larger dimension (width or height)
        """
        try:
            pixbuf = None
            ext = os.path.splitext(path)[1].lower()

            # 1) If mutagen available, try to extract embedded image bytes first
            if HAVE_MUTAGEN:
                try:
                    if ext == ".mp3":
                        tags = ID3(path)
                        for apic in tags.getall('APIC'):
                            data = apic.data
                            loader = GdkPixbuf.PixbufLoader.new()
                            loader.write(data)
                            loader.close()
                            pixbuf = loader.get_pixbuf()
                            break
                    elif ext == ".flac":
                        f = mutagen.File(path)
                        pics = getattr(f, 'pictures', [])
                        for p in pics:
                            loader = GdkPixbuf.PixbufLoader.new()
                            loader.write(p.data)
                            loader.close()
                            pixbuf = loader.get_pixbuf()
                            break
                    elif ext in (".m4a", ".mp4", ".aac"):
                        f = mutagen.File(path)
                        cov = None
                        if f is not None and hasattr(f, 'tags'):
                            # mac/mp4 covr handling
                            cov = f.tags.get('covr') if hasattr(f.tags, 'get') else None
                        if cov:
                            data = cov[0]
                            loader = GdkPixbuf.PixbufLoader.new()
                            loader.write(bytes(data))
                            loader.close()
                            pixbuf = loader.get_pixbuf()
                except Exception:
                    pixbuf = None

            # 2) If no embedded art or mutagen missing, try to load a sidecar image file:
            if pixbuf is None:
                # common cover file names in same folder
                cover_candidates = ["cover.jpg", "cover.png", "folder.jpg", "folder.png"]
                folder = os.path.dirname(path)
                for c in cover_candidates:
                    fp = os.path.join(folder, c)
                    if os.path.exists(fp):
                        try:
                            # use fast path: load already sized image from file
                            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(fp, max_size, max_size)
                            break
                        except Exception:
                            pixbuf = None

            # 3) If still None, fall back to icon
            if pixbuf is None:
                # set generic icon scaled
                try:
                    # get themed icon as pixbuf (may be small)
                    img = load_icon("audio-x-generic", size=max_size)
                    # if load_icon returned Gtk.Image with pixbuf, extract it
                    if isinstance(img, Gtk.Image) and hasattr(img, "get_pixbuf"):
                        pb = img.get_pixbuf()
                        if pb:
                            pixbuf = pb.scale_simple(max_size, max_size, GdkPixbuf.InterpType.BILINEAR)
                except Exception:
                    pixbuf = None

            # 4) If we have a pixbuf from mutagen loader or file, scale it preserving aspect ratio
            if pixbuf:
                # compute target size keeping aspect ratio
                w = pixbuf.get_width();
                h = pixbuf.get_height()
                if w > max_size or h > max_size:
                    if w >= h:
                        new_w = max_size;
                        new_h = int(h * (max_size / float(w)))
                    else:
                        new_h = max_size;
                        new_w = int(w * (max_size / float(h)))
                    scaled = pixbuf.scale_simple(new_w, new_h, GdkPixbuf.InterpType.BILINEAR)
                    self.cover_img.set_from_pixbuf(scaled)
                else:
                    self.cover_img.set_from_pixbuf(pixbuf)
            else:
                # final fallback: themed icon
                self.cover_img.set_from_icon_name("audio-x-generic", Gtk.IconSize.DIALOG)

            # ensure UI updates on main thread
            try:
                self.cover_img.show_all()
            except Exception:
                pass

        except Exception as e:
            # on any error, fallback to generic icon
            try:
                self.cover_img.set_from_icon_name("audio-x-generic", Gtk.IconSize.DIALOG)
            except Exception:
                pass

    # editing helpers (pydub)
    def _ensure_edit_stack(self) -> bool:
        if not HAVE_PYDUB:
            info_dialog(self.get_toplevel(), "Audio editor",
                        "pydub/ffmpeg topilmadi.\n\nmacOS: brew install ffmpeg\nPython: pip install pydub mutagen simpleaudio")
            return False
        return True

    def _get_current(self):
        row = self.listbox.get_selected_row()
        if not row:
            info_dialog(self.get_toplevel(), "Editor", "No track selected")
            return None
        return getattr(row, "_path", None)

    def on_edit(self, *_):
        if not self._ensure_edit_stack(): return
        src = self._get_current();
        if not src: return
        dlg = AudioTrimDialog(self.get_toplevel(), src)
        try:
            if HAVE_GST:
                d = self._probe_duration(src)
                if d: dlg.end_adj.set_upper(max(d, 10)); dlg.end_adj.set_value(d)
        except Exception:
            pass
        resp = dlg.run()
        if resp == Gtk.ResponseType.OK:
            start, end, fmt = dlg.get_values(); dlg.destroy()
            if end and end > start: self._do_trim_export(src, start, end, fmt)
            else: info_dialog(self.get_toplevel(), "Trim", "Invalid range")
        else:
            dlg.destroy()

    def _probe_duration(self, path: str) -> int:
        if HAVE_GST:
            try:
                p = Gst.ElementFactory.make("playbin", None); p.set_property("uri", Gst.filename_to_uri(path)); p.set_state(Gst.State.PAUSED)
                p.get_state(5 * Gst.SECOND)
                ok, dur = p.query_duration(Gst.Format.TIME); p.set_state(Gst.State.NULL)
                if ok and dur > 0: return int(dur / Gst.SECOND)
            except Exception:
                pass
        # fallback: try mutagen
        try:
            if HAVE_MUTAGEN:
                f = mutagen.File(path)
                if f is not None and hasattr(f.info, 'length'):
                    return int(f.info.length)
        except Exception:
            pass
        return 0

    def _do_trim_export(self, src: str, start: int, end: int, fmt: str):
        def worker():
            try:
                seg = AudioSegment.from_file(src); cut = seg[start*1000:end*1000]
                out = self._unique_out(src, suffix="_cut", fmt=fmt); params = {}
                if fmt in ("mp3","m4a"): params["bitrate"] = "192k"
                cut.export(out, format=fmt, **params)
                GLib.idle_add(lambda: info_dialog(self.get_toplevel(), "Trim", f"Saved: {out}"))
                GLib.idle_add(lambda: self._add_one(out)); GLib.idle_add(self.listbox.show_all)
            except Exception as e:
                GLib.idle_add(lambda: info_dialog(self.get_toplevel(), "Trim error", str(e)))
        threading.Thread(target=worker, daemon=True).start()

    def on_convert(self, *_):
        if not self._ensure_edit_stack(): return
        src = self._get_current();
        if not src: return
        dlg = ConvertDialog(self.get_toplevel(), src)
        if dlg.run() == Gtk.ResponseType.OK:
            fmt, br = dlg.get_values(); dlg.destroy()
            def worker():
                try:
                    seg = AudioSegment.from_file(src); out = self._unique_out(src, suffix="", fmt=fmt); params = {}
                    if fmt in ("mp3","m4a") and br: params["bitrate"] = f"{br}k" if br.isdigit() else br
                    seg.export(out, format=fmt, **params)
                    GLib.idle_add(lambda: info_dialog(self.get_toplevel(), "Convert", f"Saved: {out}"))
                    GLib.idle_add(lambda: self._add_one(out)); GLib.idle_add(self.listbox.show_all)
                except Exception as e:
                    GLib.idle_add(lambda: info_dialog(self.get_toplevel(), "Convert error", str(e)))
            threading.Thread(target=worker, daemon=True).start()
        else:
            dlg.destroy()

    def on_join(self, *_):
        if not self._ensure_edit_stack(): return
        dlg = Gtk.FileChooserDialog(title="Join audio files", parent=self.get_toplevel(), action=Gtk.FileChooserAction.OPEN)
        dlg.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, "Join", Gtk.ResponseType.OK)
        dlg.set_select_multiple(True)
        flt = Gtk.FileFilter(); flt.set_name("Audio")
        for ext in AUDIO_EXTS: flt.add_pattern(f"*{ext}")
        dlg.add_filter(flt)
        files = []
        if dlg.run() == Gtk.ResponseType.OK: files = dlg.get_filenames()
        dlg.destroy()
        if len(files) < 2: info_dialog(self.get_toplevel(), "Join", "Pick at least 2 files"); return
        def worker():
            try:
                base = AudioSegment.silent(duration=0)
                for p in files: base += AudioSegment.from_file(p)
                out = self._unique_out(files[0], suffix="_joined", fmt=_guess_fmt(files[0])); base.export(out, format=_guess_fmt(out))
                GLib.idle_add(lambda: info_dialog(self.get_toplevel(), "Join", f"Saved: {out}"))
                GLib.idle_add(lambda: self._add_one(out)); GLib.idle_add(self.listbox.show_all)
            except Exception as e:
                GLib.idle_add(lambda: info_dialog(self.get_toplevel(), "Join error", str(e)))
        threading.Thread(target=worker, daemon=True).start()

    def on_tags(self, *_):
        if not HAVE_MUTAGEN:
            info_dialog(self.get_toplevel(), "Tags", "mutagen o'rnatilmagan. pip install mutagen")
            return
        src = self._get_current();
        if not src: return
        tags = self._read_tags(src); dlg = TagEditorDialog(self.get_toplevel(), src, tags)
        if dlg.run() == Gtk.ResponseType.OK:
            new = dlg.get_values(); dlg.destroy()
            try: self._write_tags(src, new); info_dialog(self.get_toplevel(), "Tags", "Saved")
            except Exception as e: info_dialog(self.get_toplevel(), "Tags", str(e))
        else:
            dlg.destroy()

    def _read_tags(self, path: str) -> dict:
        out = {"title":"","artist":"","album":""}
        try:
            if HAVE_MUTAGEN:
                f = mutagen.File(path, easy=True)
                if f is not None:
                    out["title"] = ", ".join(f.get("title", out["title"]))
                    out["artist"] = ", ".join(f.get("artist", out["artist"]))
                    out["album"] = ", ".join(f.get("album", out["album"]))
        except Exception:
            pass
        return out

    def _write_tags(self, path: str, values: dict):
        ext = os.path.splitext(path)[1].lower()
        if ext == ".mp3":
            tags = ID3(path)
            if values.get("title"): tags.setall('TIT2', [TIT2(encoding=3, text=values["title"])])
            if values.get("artist"): tags.setall('TPE1', [TPE1(encoding=3, text=values["artist"])])
            if values.get("album"): tags.setall('TALB', [TALB(encoding=3, text=values["album"])])
            if values.get("cover"):
                with open(values["cover"], 'rb') as fh: img = fh.read()
                tags.setall('APIC', [APIC(encoding=3, mime='image/jpeg', type=3, desc='Cover', data=img)])
            tags.save(v2_version=3)
        else:
            f = mutagen.File(path)
            if f is None: return
            if values.get("title"): f["title"] = values["title"]
            if values.get("artist"): f["artist"] = values["artist"]
            if values.get("album"): f["album"] = values["album"]
            if ext == ".flac" and values.get("cover"):
                with open(values["cover"], 'rb') as fh: data = fh.read()
                pic = Picture(); pic.data = data; pic.type = 3; pic.mime = 'image/jpeg'
                if not hasattr(f, 'pictures'): f.add_picture(pic)
                else: f.clear_pictures(); f.add_picture(pic)
            f.save()

    def _unique_out(self, src: str, suffix: str, fmt: str) -> str:
        base, _ = os.path.splitext(src); cand = f"{base}{suffix}.{fmt}"; i = 1
        while os.path.exists(cand): cand = f"{base}{suffix}-{i}.{fmt}"; i += 1
        return cand

    # ---------------- simpleaudio wrapper ----------------
    def _sa_play(self, segment: 'AudioSegment'):
        # Stop existing
        if not HAVE_SIMPLEAUDIO:
            # fallback to blocking pydub.play (if available) but warn
            try:
                from pydub.playback import play
                threading.Thread(target=play, args=(segment,), daemon=True).start()
                return
            except Exception:
                info_dialog(self.get_toplevel(), "Playback", "No simpleaudio available for fallback playback")
                return
        try:
            # ensure sample properties are okay
            seg = segment
            channels = seg.channels
            sample_width = seg.sample_width
            frame_rate = seg.frame_rate
            raw = seg.raw_data
            wave_obj = simpleaudio.WaveObject(raw, num_channels=channels, bytes_per_sample=sample_width, sample_rate=frame_rate)
            play_obj = wave_obj.play()
            # attach .time property to mimic elapsed time later (simpleaudio doesn't expose exact elapsed reliably)
            self._sa_play_obj = play_obj
        except Exception as e:
            info_dialog(self.get_toplevel(), "Playback error", str(e))
