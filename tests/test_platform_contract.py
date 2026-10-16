"""Проверка экспортов и обязательных аргументов платформенной реализации."""

import importlib
import inspect
import sys
import unittest

from plugbackup.plat import base

try:
    import gi
    gi.require_version("UDisks", "2.0")
    from gi.repository import UDisks  # noqa: F401
    GI_AVAILABLE = True
except (ImportError, ValueError):
    GI_AVAILABLE = False

ON_LINUX = sys.platform.startswith("linux")
IMPLEMENTATION = "linux" if ON_LINUX else "windows"


class PlatformContractTests(unittest.TestCase):
    def test_windows_platform_modules_import_and_export_contract_on_any_os(self):
        modules = {}
        for module_name in ("devices", "mounts", "secrets", "autostart", "system", "paths", "deps"):
            module = importlib.import_module("plugbackup.plat.windows." + module_name)
            modules[module_name] = module
            missing = sorted(name for name in base.MODULE_APIS[module_name] if not hasattr(module, name))
            self.assertEqual(missing, [], "windows.{} не экспортирует {}".format(module_name, missing))
        for qualified_name, required in base.REQUIRED_PARAMETERS.items():
            if qualified_name.split(".", 1)[0] in modules:
                self._check_signature(modules, qualified_name, required)

    @unittest.skipUnless(not ON_LINUX or GI_AVAILABLE, "для проверки Linux API нужен GI UDisks2")
    def test_active_platform_modules_export_the_declared_api(self):
        modules = {}
        expected = {}
        for module_name, names in base.MODULE_APIS.items():
            expected[module_name] = set(names)
        if ON_LINUX:
            for source in (base.GTK_MODULE_APIS, base.LINUX_EXTRA_APIS):
                for module_name, names in source.items():
                    expected.setdefault(module_name, set()).update(names)

        for module_name, names in expected.items():
            module = importlib.import_module("plugbackup.plat.{}.{}".format(IMPLEMENTATION, module_name))
            modules[module_name] = module
            missing = sorted(name for name in names if not hasattr(module, name))
            self.assertEqual(missing, [], "{} не экспортирует {}".format(module_name, missing))

        for qualified_name, expected_required in base.REQUIRED_PARAMETERS.items():
            self._check_signature(modules, qualified_name, expected_required)
        if ON_LINUX:
            for qualified_name, expected_required in base.GTK_REQUIRED_PARAMETERS.items():
                self._check_signature(modules, qualified_name, expected_required)
            for qualified_name, expected_required in base.LINUX_REQUIRED_PARAMETERS.items():
                self._check_signature(modules, qualified_name, expected_required)

    def _check_signature(self, modules, qualified_name, expected_required):
        module_name, attribute_path = qualified_name.split(".", 1)
        value = modules[module_name]
        for part in attribute_path.split("."):
            value = getattr(value, part)
        signature = inspect.signature(value)
        parameters = list(signature.parameters.values())
        if parameters and parameters[0].name == "self":
            parameters = parameters[1:]
        required = [parameter.name for parameter in parameters
                    if parameter.default is inspect.Parameter.empty
                    and parameter.kind in (inspect.Parameter.POSITIONAL_ONLY,
                                           inspect.Parameter.POSITIONAL_OR_KEYWORD,
                                           inspect.Parameter.KEYWORD_ONLY)]
        self.assertEqual(required, list(expected_required), "сигнатура {}: {}".format(qualified_name, signature))
