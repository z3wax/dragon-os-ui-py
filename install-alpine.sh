#!/bin/sh
# DragonOS UI - Alpine Linux one-shot setup
# Usage:  doas sh install-alpine.sh
set -e

if ! command -v apk >/dev/null 2>&1; then
  echo "[!] This script is for Alpine Linux (apk not found)."
  exit 1
fi

if [ "$(id -u)" != 0 ]; then
  echo "[i] Needs root: rerun with 'doas sh install-alpine.sh'"
  exec doas "$0" "$@" 2>/dev/null || exec sudo "$0" "$@"
fi

echo "[1/4] Refreshing repositories..."
apk update

echo "[2/4] Installing GTK3 + PyGObject stack..."
apk add --no-progress \
  python3 py3-gobject3 py3-cairo \
  gtk+3.0 gdk-pixbuf librsvg \
  adwaita-icon-theme gnome-icon-theme ttf-dejavu \
  xdg-utils xdg-user-dirs \
  mesa-gl wayland wayland-protocols \
  bash

# Optional extras (best effort, ignore failures)
apk add --no-progress vte3 py3-vte 2>/dev/null || true       # real terminal emulator
apk add --no-progress polkit elogind seatd 2>/dev/null || true # power actions for users
apk add --no-progress gthumb xdg-desktop-portal 2>/dev/null || true
apk add --no-progress py3-pyte 2>/dev/null || true

echo "[3/4] Registering launcher 'dragon-os'..."
HERE="$(cd "$(dirname "$0")" && pwd)"
BIN="$HERE/dragon-os"
[ -f "$BIN" ] || BIN="/opt/dragon-os-ui-py/dragon-os"

mkdir -p /usr/local/bin
cat > /usr/local/bin/dragon-os <<EOF
#!/bin/sh
exec python3 "$HERE/main.py" "\$@"
EOF
chmod 755 /usr/local/bin/dragon-os

mkdir -p /usr/share/applications
sed -e "s|^Exec=.*|Exec=$BIN|" -e "s|^TryExec=.*|TryExec=$BIN|" \
  "$HERE/dragon-os.desktop" > /usr/share/applications/dragon-os.desktop \
  2>/dev/null || true

echo "[4/4] Done."
cat <<EOF

  Start the desktop:            dragon-os
  Or run manually:              cd $HERE && python3 main.py

  Default login:                admin / 1234   (or 'guest', empty password)

EOF
