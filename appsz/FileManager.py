# ---------- FileManager ----------
import os
import stat
import time
import shutil
import subprocess
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib

from helpers import info_dialog, make_target_entries, load_icon, ensure_desktop_folder

class FileManager(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.set_border_width(8)
        self.current_path = os.path.expanduser("~")

        # Header: path entry + search + refresh
        header = Gtk.Box(spacing=8)
        self.path_entry = Gtk.Entry()
        self.path_entry.set_text(self.current_path)
        self.path_entry.set_hexpand(True)
        self.path_entry.connect("activate", lambda *_: self.change_dir(self.path_entry.get_text()))
        header.pack_start(self.path_entry, True, True, 0)

        search = Gtk.SearchEntry()
        search.set_placeholder_text("Search (Ctrl+F)")
        search.connect("search-changed", lambda w: self._filter_files(w.get_text()))
        header.pack_start(search, False, False, 0)

        refresh = Gtk.Button()
        try:
            refresh.set_image(load_icon("view-refresh", size=16))
        except Exception:
            refresh.set_label("⟳")
        refresh.connect("clicked", lambda *_: self.populate_files())
        header.pack_start(refresh, False, False, 0)
        self.pack_start(header, False, False, 0)

        # Main split: TreeView (left) + Preview (right)
        paned = Gtk.Paned.new(Gtk.Orientation.HORIZONTAL)
        self.pack_start(paned, True, True, 0)

        # Left: TreeView list
        self.store = Gtk.ListStore(str, bool, int)
        self.tree = Gtk.TreeView(model=self.store)
        self.tree.set_headers_visible(True)
        name_r = Gtk.CellRendererText()
        col_name = Gtk.TreeViewColumn("Name", name_r, text=0)
        col_type = Gtk.TreeViewColumn("IsDir", Gtk.CellRendererText(), text=1)
        col_size = Gtk.TreeViewColumn("Size", Gtk.CellRendererText(), text=2)
        self.tree.append_column(col_name)
        self.tree.append_column(col_type)
        self.tree.append_column(col_size)
        self.tree.connect("row-activated", self.on_row_activated)
        sc_left = Gtk.ScrolledWindow(); sc_left.add(self.tree)
        paned.add1(sc_left)

        # Right: Preview pane
        preview_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.preview = Gtk.TextView(); self.preview.set_editable(False)
        psc = Gtk.ScrolledWindow(); psc.set_min_content_width(360); psc.add(self.preview)
        preview_box.pack_start(psc, True, True, 0)

        # quick actions under preview
        actions = Gtk.Box(spacing=6)
        open_btn = Gtk.Button(label="Open / Preview"); open_btn.connect("clicked", self.on_open_selected)
        reveal_btn = Gtk.Button(label="Reveal"); reveal_btn.connect("clicked", self._reveal_selected)
        rename_btn = Gtk.Button(label="Rename"); rename_btn.connect("clicked", self._rename_selected)
        del_btn = Gtk.Button(label="Delete"); del_btn.connect("clicked", self._delete_selected)
        for b in (open_btn, reveal_btn, rename_btn, del_btn):
            actions.pack_start(b, False, False, 0)
        preview_box.pack_start(actions, False, False, 0)

        paned.add2(preview_box)

        # drag source
        self.tree.enable_model_drag_source(
            Gdk.ModifierType.BUTTON1_MASK,
            make_target_entries([("text/plain", 0, 0)]),
            Gdk.DragAction.COPY
        )
        self.tree.connect("drag-data-get", self.on_drag_data_get)

        self.populate_files()

    def populate_files(self):
        self.store.clear()
        try:
            entries = sorted(os.listdir(self.current_path), key=lambda x: x.lower())
        except Exception as e:
            info_dialog(self.get_toplevel(), "Error", f"Cannot open folder:\n{e}")
            entries = []
        for name in entries:
            full = os.path.join(self.current_path, name)
            try:
                st = os.stat(full)
                is_dir = stat.S_ISDIR(st.st_mode)
                size = st.st_size if not is_dir else 0
            except Exception:
                is_dir = False; size = 0
            self.store.append([name, is_dir, size])
        self.path_entry.set_text(self.current_path)

    def change_dir(self, path):
        if not path: return
        if os.path.exists(path) and os.path.isdir(path):
            self.current_path = path
            self.populate_files()
        else:
            info_dialog(self.get_toplevel(), "Error", f"{path} not found")

    def get_selected(self):
        sel = self.tree.get_selection(); model, iter_ = sel.get_selected()
        if iter_:
            name = model.get_value(iter_, 0); is_dir = model.get_value(iter_, 1)
            return os.path.join(self.current_path, name), is_dir
        return None, None

    def on_row_activated(self, tree, path, col):
        model = tree.get_model(); iter_ = model.get_iter(path)
        name = model.get_value(iter_, 0); is_dir = model.get_value(iter_, 1)
        full = os.path.join(self.current_path, name)
        if is_dir:
            self.change_dir(full)
        else:
            self.preview_file(full)

    def on_open_selected(self, *_):
        full, is_dir = self.get_selected()
        if not full:
            info_dialog(self.get_toplevel(), "Note", "No file selected"); return
        if is_dir:
            self.change_dir(full)
        else:
            self.preview_file(full)

    def preview_file(self, path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = f.read(20000)
            buf = self.preview.get_buffer(); buf.set_text(data)
        except Exception:
            buf = self.preview.get_buffer(); buf.set_text("Preview not available (binary or non-UTF-8).")

    def _reveal_selected(self, *_):
        full, _ = self.get_selected()
        if not full: return
        try:
            if sys.platform == "darwin":
                subprocess.call(["open", "-R", full])
            elif sys.platform.startswith("win"):
                subprocess.call(["explorer", "/select,", full])
            else:
                subprocess.call(["xdg-open", os.path.dirname(full)])
        except Exception as e:
            info_dialog(self.get_toplevel(), "Error", str(e))

    def _rename_selected(self, *_):
        full, _ = self.get_selected()
        if not full: return
        dirname = os.path.dirname(full)
        old = os.path.basename(full)
        dlg = Gtk.MessageDialog(transient_for=self.get_toplevel(), flags=0,
                                message_type=Gtk.MessageType.QUESTION, buttons=Gtk.ButtonsType.OK_CANCEL,
                                text=f"Rename {old} to:")
        entry = Gtk.Entry(); entry.set_text(old)
        dlg.get_content_area().pack_start(entry, True, True, 0); dlg.show_all()
        res = dlg.run(); dlg.destroy()
        if res == Gtk.ResponseType.OK:
            new = entry.get_text().strip()
            if new:
                try:
                    os.rename(full, os.path.join(dirname, new)); self.populate_files()
                except Exception as e:
                    info_dialog(self.get_toplevel(), "Error", str(e))

    def _delete_selected(self, *_):
        full, _ = self.get_selected()
        if not full: return
        dlg = Gtk.MessageDialog(transient_for=self.get_toplevel(), flags=0,
                               message_type=Gtk.MessageType.WARNING, buttons=Gtk.ButtonsType.OK_CANCEL,
                               text=f"Delete {os.path.basename(full)}?")
        res = dlg.run(); dlg.destroy()
        if res == Gtk.ResponseType.OK:
            try:
                trash_dir = os.path.expanduser("~/.local/share/Trash/files")
                os.makedirs(trash_dir, exist_ok=True)
                dest = os.path.join(trash_dir, os.path.basename(full))
                i = 0; base = dest
                while os.path.exists(dest):
                    i += 1; dest = f"{base}.{i}"
                shutil.move(full, dest); info_dialog(self.get_toplevel(), "Deleted", f"Moved to Trash:\n{dest}")
                self.populate_files()
            except Exception as e:
                info_dialog(self.get_toplevel(), "Error", str(e))

    def on_drag_data_get(self, treeview, drag_context, selection_data, info, time):
        sel = treeview.get_selection(); model, iter_ = sel.get_selected()
        if not iter_:
            selection_data.set_text("", -1); return
        name = model.get_value(iter_, 0)
        full = os.path.join(self.current_path, name)
        selection_data.set_text(full, -1)

    def _filter_files(self, q):
        # Simple client-side filter: repopulate tree with matching names
        q = (q or "").strip().lower()
        if not q:
            self.populate_files()
            return
        self.store.clear()
        try:
            for name in sorted(os.listdir(self.current_path), key=lambda x: x.lower()):
                if q in name.lower():
                    full = os.path.join(self.current_path, name)
                    try:
                        st = os.stat(full); is_dir = stat.S_ISDIR(st.st_mode); size = st.st_size if not is_dir else 0
                    except Exception:
                        is_dir = False; size = 0
                    self.store.append([name, is_dir, size])
        except Exception as e:
            info_dialog(self.get_toplevel(), "Error", str(e))
