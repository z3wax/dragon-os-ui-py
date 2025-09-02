# helpers.py
import gi, os
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, GdkPixbuf

def make_target_entries(tuples):
    """
    tuples: list of (target_str, flags_int, info_int)
    returns: list of Gtk.TargetEntry
    """
    return [Gtk.TargetEntry.new(t, flags, info) for (t, flags, info) in tuples]

def info_dialog(parent, title, text):
    dlg = Gtk.MessageDialog(transient_for=parent, flags=0,
                            message_type=Gtk.MessageType.INFO,
                            buttons=Gtk.ButtonsType.OK,
                            text=title)
    dlg.format_secondary_text(text)
    dlg.run(); dlg.destroy()

def load_icon(icon_ref, size=48):
    # icon_ref may be path or icon name
    if isinstance(icon_ref, str) and os.path.exists(icon_ref):
        try:
            pb = GdkPixbuf.Pixbuf.new_from_file_at_size(icon_ref, size, size)
            return Gtk.Image.new_from_pixbuf(pb)
        except Exception:
            pass
    try:
        return Gtk.Image.new_from_icon_name(icon_ref, Gtk.IconSize.DIALOG)
    except Exception:
        return Gtk.Image.new_from_icon_name("application-x-executable", Gtk.IconSize.DIALOG)

def ensure_desktop_folder(folder_path):
    try:
        os.makedirs(folder_path, exist_ok=True)
    except Exception:
        pass
