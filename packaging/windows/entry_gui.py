"""Запуск Qt-интерфейса, если он добавлен в проект."""
import importlib
import sys


def main():
    try:
        module = importlib.import_module("plugbackup.qt.app")
        app_main = getattr(module, "main")
    except (ImportError, AttributeError):
        message = (
            "Оконный интерфейс Plug & Backup пока не включён в эту сборку. "
            "Для командной строки используйте plugbackup-cli.exe."
        )
        if sys.platform == "win32":
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, message, "Plug & Backup", 0x10)
        else:
            print(message, file=sys.stderr)
        return 1
    return app_main()


if __name__ == "__main__":
    sys.exit(main())
