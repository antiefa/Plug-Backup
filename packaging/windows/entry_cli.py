"""Точка входа CLI без импорта GTK-приложения."""
import sys

from plugbackup import cli


def main(argv):
    if "--version" in argv:
        return cli.cmd_version()
    if "--list" in argv:
        return cli.cmd_list()
    for index, argument in enumerate(argv):
        if argument == "--backup":
            return cli.cmd_backup(argv[index + 1]) if index + 1 < len(argv) else 2
        if argument.startswith("--backup="):
            return cli.cmd_backup(argument.split("=", 1)[1])
    if "--check" in argv:
        return cli.cmd_check()
    print("Использование: plugbackup-cli.exe --version|--check|--list|--backup PROFILE", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
