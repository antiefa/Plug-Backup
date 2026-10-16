import sys


def _cli_command(argv):
    """Запускает команды без окна до импорта GTK-приложения."""
    from plugbackup import cli

    if "--version" in argv:
        return cli.cmd_version()
    if "--list" in argv:
        return cli.cmd_list()
    for index, argument in enumerate(argv):
        if argument == "--backup":
            if index + 1 < len(argv):
                return cli.cmd_backup(argv[index + 1])
            return None
        if argument.startswith("--backup="):
            return cli.cmd_backup(argument.split("=", 1)[1])
    if "--check" in argv:
        return cli.cmd_check()
    return None


result = _cli_command(sys.argv[1:])
if result is None:
    from plugbackup.app import main

    result = main()
sys.exit(result)
