#!/bin/sh
# Сборка пакета dist/plugbackup_<версия>_all.deb.
# Нужны: dpkg-deb, msgfmt (gettext), rsvg-convert (librsvg2-bin).
set -eu
cd "$(dirname "$0")"

VERSION=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' plugbackup/__init__.py)
APP_ID=io.github.plugbackup.PlugBackup
PKG="plugbackup_${VERSION}_all"
STAGE="build/deb/$PKG"

for tool in dpkg-deb msgfmt rsvg-convert; do
    command -v "$tool" >/dev/null || { echo "Не найдена утилита: $tool" >&2; exit 1; }
done

rm -rf "build/deb"
mkdir -p "$STAGE/DEBIAN" "$STAGE/usr/bin" "$STAGE/usr/lib/plugbackup" \
    "$STAGE/usr/share/applications" "$STAGE/usr/share/icons/hicolor/scalable/apps" dist

# Код приложения (без кэша байт-кода)
find plugbackup -name '*.py' -exec cp --parents {} "$STAGE/usr/lib/plugbackup/" \;

cat > "$STAGE/usr/bin/plugbackup" <<'EOF'
#!/usr/bin/python3
import sys

sys.path.insert(0, "/usr/lib/plugbackup")

from plugbackup.app import main

sys.exit(main())
EOF

# Ярлык
cp "data/$APP_ID.desktop" "$STAGE/usr/share/applications/"
if command -v desktop-file-validate >/dev/null; then
    desktop-file-validate "$STAGE/usr/share/applications/$APP_ID.desktop"
fi

# Иконка: SVG + PNG нужных размеров
cp "data/icons/$APP_ID.svg" "$STAGE/usr/share/icons/hicolor/scalable/apps/"
for size in 16 24 32 48 64 128 256; do
    dir="$STAGE/usr/share/icons/hicolor/${size}x${size}/apps"
    mkdir -p "$dir"
    rsvg-convert -w "$size" -h "$size" "data/icons/$APP_ID.svg" -o "$dir/$APP_ID.png"
done

# Свои иконки действий (одинаковы в любой теме значков)
mkdir -p "$STAGE/usr/share/icons/hicolor/scalable/actions"
cp data/icons/actions/*.svg "$STAGE/usr/share/icons/hicolor/scalable/actions/"
# Значки трея: по имени, чтобы панель Cinnamon перекрашивала их под свою тему
mkdir -p "$STAGE/usr/share/icons/hicolor/scalable/status"
cp data/icons/status/*.svg "$STAGE/usr/share/icons/hicolor/scalable/status/"

# Переводы
for po in po/*.po; do
    lang=$(basename "$po" .po)
    dir="$STAGE/usr/share/locale/$lang/LC_MESSAGES"
    mkdir -p "$dir"
    msgfmt --check -o "$dir/plugbackup.mo" "$po"
done

# Права: каталоги 755, файлы 644, команда 755
find "$STAGE" -type d -exec chmod 755 {} +
find "$STAGE" -type f -exec chmod 644 {} +
chmod 755 "$STAGE/usr/bin/plugbackup"

INSTALLED_SIZE=$(du -sk --exclude=DEBIAN "$STAGE" | cut -f1)
cat > "$STAGE/DEBIAN/control" <<EOF
Package: plugbackup
Version: $VERSION
Section: utils
Priority: optional
Architecture: all
Installed-Size: $INSTALLED_SIZE
Maintainer: Plug & Backup <plugbackup@localhost>
Depends: python3 (>= 3.12), python3-gi, gir1.2-gtk-3.0, gir1.2-udisks-2.0, udisks2, gir1.2-notify-0.7, gir1.2-secret-1, rsync, cryptsetup, gir1.2-xapp-1.0 | gir1.2-ayatanaappindicator3-0.1
Recommends: gocryptfs, fuse3, gnome-keyring
Description: back up selected folders to removable drives
 Plug & Backup copies selected folders to USB flash drives, SD cards and
 external disks with one click or automatically when the drive is inserted.
 Backup profiles are bound to a drive by its identifier; only changes are
 copied (rsync), with optional version history and encryption.
EOF

dpkg-deb --root-owner-group --build "$STAGE" "dist/$PKG.deb"
echo "Готово: dist/$PKG.deb"
