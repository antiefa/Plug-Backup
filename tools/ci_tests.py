"""Запуск тестов в CI: обычный вывод unittest плюс аннотации GitHub по упавшим тестам.

Аннотации (строки ``::error ...``) видны на странице запуска и через открытый API GitHub,
поэтому причину падения можно прочитать без доступа к полному журналу.
"""
from __future__ import annotations

import sys
import unittest

MAX_ANNOTATIONS = 10        # GitHub показывает не больше 10 ошибок на шаг
MAX_LINES = 25              # хвост трассировки в одной аннотации


def _escape(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "").replace("\n", "%0A")


def main() -> int:
    suite = unittest.defaultTestLoader.discover("tests", top_level_dir=".")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    problems = [("FAIL", test, trace) for test, trace in result.failures]
    problems += [("ERROR", test, trace) for test, trace in result.errors]
    problems += [("UNEXPECTED SUCCESS", test, "") for test in result.unexpectedSuccesses]
    for kind, test, trace in problems[:MAX_ANNOTATIONS]:
        tail = "\n".join(trace.strip().splitlines()[-MAX_LINES:])
        print(f"::error title={_escape(kind + ': ' + test.id())}::{_escape(tail)}")
    if len(problems) > MAX_ANNOTATIONS:
        print(f"::error title=More failures::{len(problems) - MAX_ANNOTATIONS} more, see the log")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
