"""Компилятор простого подмножества PO в GNU MO на стандартной библиотеке."""
import ast
import struct
import sys
from pathlib import Path


def read_po(path):
    entries = {}
    fields = {}
    active = None

    def finish():
        if "msgid" not in fields:
            return
        msgid = fields["msgid"]
        if "msgctxt" in fields:
            msgid = fields["msgctxt"] + "\x04" + msgid
        if "msgid_plural" in fields:
            original = msgid + "\0" + fields["msgid_plural"]
            translated = "\0".join(fields[k] for k in sorted(fields) if k.startswith("msgstr["))
        else:
            original = msgid
            translated = fields.get("msgstr", "")
        if original == "":
            translated = "".join(line for line in translated.splitlines(keepends=True)
                                  if not line.startswith("POT-Creation-Date:"))
        entries[original] = translated

    for raw in path.read_text(encoding="utf-8-sig").splitlines() + [""]:
        line = raw.strip()
        if not line or line.startswith("#"):
            finish()
            fields = {}
            active = None
            continue
        if line.startswith('"'):
            if active is None:
                raise ValueError("строка продолжения без поля: {}".format(line))
            fields[active] += ast.literal_eval(line)
            continue
        for prefix in ("msgctxt", "msgid_plural", "msgid", "msgstr"):
            if line.startswith(prefix) and (len(line) == len(prefix) or line[len(prefix)].isspace() or line[len(prefix)] == "["):
                key, value = line.split(None, 1)
                active = key
                fields[key] = ast.literal_eval(value)
                break
        else:
            raise ValueError("неподдерживаемая строка PO: {}".format(line))
    return entries


def write_mo(entries, target):
    messages = sorted((key.encode("utf-8"), value.encode("utf-8")) for key, value in entries.items())
    count = len(messages)
    header_size = 7 * 4
    original_table = header_size
    translated_table = original_table + count * 8
    string_offset = translated_table + count * 8
    originals = bytearray()
    translations = bytearray()
    original_refs = []
    translated_refs = []
    for original, translated in messages:
        original_refs.append((len(original), string_offset + len(originals)))
        originals.extend(original + b"\0")
    translation_start = string_offset + len(originals)
    for original, translated in messages:
        translated_refs.append((len(translated), translation_start + len(translations)))
        translations.extend(translated + b"\0")
    output = bytearray(struct.pack("<7I", 0x950412DE, 0, count, original_table, translated_table, 0, 0))
    for length, offset in original_refs:
        output.extend(struct.pack("<2I", length, offset))
    for length, offset in translated_refs:
        output.extend(struct.pack("<2I", length, offset))
    output.extend(originals)
    output.extend(translations)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(output)


def main(argv):
    if len(argv) != 2:
        print("Использование: build_mo.py вход.po выход.mo", file=sys.stderr)
        return 2
    write_mo(read_po(Path(argv[0])), Path(argv[1]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
