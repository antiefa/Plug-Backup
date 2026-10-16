#!/bin/sh
# Обновляет шаблон переводов po/plugbackup.pot из исходников и сливает его в po/*.po.
set -eu
cd "$(dirname "$0")/.."
VERSION=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' plugbackup/__init__.py)
find plugbackup -name '*.py' | sort | xargs xgettext \
    --language=Python --keyword=_ --keyword=ngettext:1,2 --from-code=UTF-8 \
    --package-name=plugbackup --package-version="$VERSION" \
    --add-comments=TRANSLATORS: -o po/plugbackup.pot
for po in po/*.po; do
    [ -e "$po" ] || continue
    msgmerge --quiet --update --backup=none "$po" po/plugbackup.pot
done
