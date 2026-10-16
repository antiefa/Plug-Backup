from __future__ import annotations

import ctypes
import struct
import sys
import types
import unittest
from unittest import mock

from plugbackup.plat.windows import win32 as w


class StubFunction:
    def __init__(self, callback=None, result=1):
        self.callback = callback
        self.result = result
        self.argtypes = None
        self._restype = None
        self.restype_configured = False
        self.calls = 0

    @property
    def restype(self):
        return self._restype

    @restype.setter
    def restype(self, value):
        self._restype = value
        self.restype_configured = True

    def __call__(self, *args):
        self.calls += 1
        if self.callback:
            return self.callback(*args)
        return self.result


class StubDLL:
    def __init__(self, **functions):
        self.functions = {name: value if isinstance(value, StubFunction) else StubFunction(value)
                          for name, value in functions.items()}

    def __getattr__(self, name):
        return self.functions.setdefault(name, StubFunction())


class FakeWin32Tests(unittest.TestCase):
    def setUp(self):
        self.last_error = [0]
        self.dummy = mock.patch.object(w.sys, "platform", "win32")
        self.dummy.start()
        self.addCleanup(self.dummy.stop)
        self.err_patch = mock.patch.object(w.ctypes, "get_last_error", lambda: self.last_error[0], create=True)
        self.err_patch.start()
        self.addCleanup(self.err_patch.stop)
        self.dlls = {}
        self.dll_patch = mock.patch.object(w, "_dll", side_effect=self.get_dll)
        self.dll_patch.start()
        self.addCleanup(self.dll_patch.stop)

    def get_dll(self, name, **_kwargs):
        return self.dlls.setdefault(name, StubDLL())

    @staticmethod
    def set_u32(pointer, value):
        ctypes.cast(pointer, ctypes.POINTER(w.DWORD)).contents.value = value

    @staticmethod
    def set_u64(pointer, value):
        ctypes.cast(pointer, ctypes.POINTER(ctypes.c_ulonglong)).contents.value = value

    def assert_declared(self):
        for dll in self.dlls.values():
            for fn in dll.functions.values():
                self.assertIsNotNone(fn.argtypes)
                self.assertTrue(fn.restype_configured)

    def test_enumeration_volume_success_and_errors(self):
        volume_path = "\\\\?\\Volume{test}\\"

        def first(buf, _size):
            buf.value = volume_path
            return 41

        def next_volume(_handle, _buf, _size):
            self.last_error[0] = w.ERROR_NO_MORE_FILES
            return 0

        def paths(_path, buf, _size, needed):
            if buf is None:
                self.set_u32(needed, 5)
                self.last_error[0] = w.ERROR_MORE_DATA
                return 0
            for i, ch in enumerate("C:\\\0\0"):
                buf[i] = ch
            self.set_u32(needed, 5)
            return 1

        def volume_info(_path, label, _label_size, serial, maxlen, flags, fs, _fs_size):
            label.value = "DATA"
            fs.value = "NTFS"
            self.set_u32(serial, 0x1234ABCD)
            self.set_u32(maxlen, 255)
            self.set_u32(flags, w.FILE_CASE_SENSITIVE_SEARCH)
            return 1

        def free_space(_path, available, total, _free_total):
            self.set_u64(available, 100)
            self.set_u64(total, 200)
            return 1

        self.dlls["kernel32"] = StubDLL(
            FindFirstVolumeW=first,
            FindNextVolumeW=next_volume,
            FindVolumeClose=lambda _h: 1,
            GetVolumePathNamesForVolumeNameW=paths,
            GetVolumeInformationW=volume_info,
            GetDriveTypeW=lambda _path: w.DRIVE_FIXED,
            GetDiskFreeSpaceExW=free_space,
        )
        self.assertEqual(w.enumerate_volume_paths(), (volume_path,))
        item = w.query_volume(volume_path)
        self.assertEqual(item.access_points, ("C:\\",))
        self.assertEqual(item.serial32, 0x1234ABCD)
        self.assertEqual((item.total_bytes, item.free_bytes), (200, 100))
        self.assert_declared()

        self.dlls["kernel32"].functions["FindFirstVolumeW"].callback = lambda *_args: w.INVALID_HANDLE_VALUE
        with self.assertRaises(OSError):
            w.enumerate_volume_paths()

        def volume_info_failure(*_args):
            self.last_error[0] = 5
            return 0

        self.dlls["kernel32"].functions["FindFirstVolumeW"].callback = first
        self.dlls["kernel32"].functions["GetVolumeInformationW"].callback = volume_info_failure
        self.assertEqual(w.query_volume(volume_path).error, 5)

    def test_system_volume_lookup(self):
        path = "\\\\?\\Volume{system}\\"

        def first(buf, _size):
            buf.value = path
            return 9

        def next_volume(_h, _buf, _size):
            self.last_error[0] = w.ERROR_NO_MORE_FILES
            return 0

        self.dlls["kernel32"] = StubDLL(
            FindFirstVolumeW=first,
            FindNextVolumeW=next_volume,
            FindVolumeClose=lambda _h: 1,
            GetWindowsDirectoryW=lambda buf, _size: setattr(buf, "value", "C:\\Windows") or 10,
            GetVolumePathNameW=lambda _path, buf, _size: setattr(buf, "value", "C:\\") or 1,
            GetVolumeNameForVolumeMountPointW=lambda _mount, buf, _size: setattr(buf, "value", path) or 1,
        )
        with mock.patch.dict(w.os.environ, {"USERPROFILE": "C:\\Users\\tester"}):
            self.assertEqual(w.system_volume_paths(), frozenset((path,)))
        self.assert_declared()

    def test_storage_and_ntfs_ioctls_success_and_create_failure(self):
        descriptor = bytearray(64)
        struct.pack_into("<II4B4I2I", descriptor, 0, 64, 64, 0, 0, 1, 1, 36, 41, 0, 0, 7, 0)
        descriptor[36:41] = b"ACME\0"
        descriptor[41:48] = b"DriveX\0"

        def ioctl(_h, code, _inbuf, _insize, output, _outsize, returned, _overlapped):
            if code == 0x002D1400:
                ctypes.memmove(output, bytes(descriptor), len(descriptor))
                self.set_u32(returned, len(descriptor))
                return 1
            if code == 0x002D1080:
                ctypes.memmove(output, struct.pack("<III", 7, 4, 0), 12)
                self.set_u32(returned, 12)
                return 1
            if code == 0x00090064:
                target = ctypes.cast(output, ctypes.POINTER(w.NTFS_VOLUME_DATA_BUFFER)).contents
                target.VolumeSerialNumber = 0x123456789ABCDEF0
                self.set_u32(returned, ctypes.sizeof(target))
                return 1
            return 0

        self.dlls["kernel32"] = StubDLL(
            CreateFileW=lambda *_args: 123,
            CloseHandle=lambda _h: 1,
            DeviceIoControl=ioctl,
        )
        info = w.query_storage("\\\\?\\Volume{abc}\\")
        self.assertEqual((info.vendor, info.product, info.bus_type, info.disk_number), ("ACME", "DriveX", 7, 4))
        self.assertEqual(w.ntfs_volume_serial("\\\\?\\Volume{abc}\\"), 0x123456789ABCDEF0)
        self.assert_declared()

        self.dlls["kernel32"].functions["DeviceIoControl"].callback = lambda *_args: 0
        self.last_error[0] = 5
        self.assertEqual(w.query_storage("\\\\?\\Volume{abc}\\").error, 5)
        self.assertIsNone(w.ntfs_volume_serial("\\\\?\\Volume{abc}\\"))
        self.dlls["kernel32"].functions["CreateFileW"].callback = lambda *_args: w.INVALID_HANDLE_VALUE
        self.last_error[0] = 5
        self.assertEqual(w.query_storage("\\\\?\\Volume{abc}\\").error, 5)
        self.assertIsNone(w.ntfs_volume_serial("\\\\?\\Volume{abc}\\"))

    def test_eject_disk_success_and_not_found(self):
        dev_guid = "\\\\?\\disk#sample"
        disk_no = 12
        enums = [0]

        def get_devs(*_args):
            return 500

        def enum(_set, _devinfo, _guid, index, iface):
            enums[0] += 1
            if index:
                self.last_error[0] = 259
                return 0
            iface._obj.Flags = 1
            return 1

        def detail(_set, _iface, raw, raw_size, required, devinfo):
            if raw is None:
                self.set_u32(required, 4 + (len(dev_guid) + 1) * 2)
                return 0
            struct.pack_into("<I", raw, 0, 8)
            ctypes.memmove(ctypes.addressof(raw) + 4, (dev_guid + "\0").encode("utf-16-le"), (len(dev_guid) + 1) * 2)
            devinfo._obj.DevInst = 100
            return 1

        def ioctl(_h, _code, _inbuf, _insize, output, _size, _returned, _overlap):
            ctypes.memmove(output, struct.pack("<III", 7, disk_no, 0), 12)
            return 1

        def parent(parent_ptr, devinst, _flags):
            self.assertEqual(devinst, 100)
            ctypes.cast(parent_ptr, ctypes.POINTER(w.DWORD)).contents.value = 200
            return 0

        def eject(parent_inst, veto_ptr, veto_name, _name_size, _flags):
            self.assertEqual(parent_inst, 200)
            ctypes.cast(veto_ptr, ctypes.POINTER(w.DWORD)).contents.value = 0
            veto_name.value = ""
            return 0

        self.dlls["setupapi"] = StubDLL(
            SetupDiGetClassDevsW=get_devs,
            SetupDiEnumDeviceInterfaces=enum,
            SetupDiGetDeviceInterfaceDetailW=detail,
            SetupDiDestroyDeviceInfoList=lambda _set: 1,
        )
        self.dlls["cfgmgr32"] = StubDLL(CM_Get_Parent=parent, CM_Request_Device_EjectW=eject)
        self.dlls["kernel32"] = StubDLL(
            CreateFileW=lambda *_args: 33,
            CloseHandle=lambda _h: 1,
            DeviceIoControl=ioctl,
        )
        result = w.eject_disk(disk_no)
        self.assertTrue(result.success)
        self.assertEqual(result.configret, 0)
        self.assert_declared()

        def refused_eject(_parent, veto_ptr, veto_name, _name_size, _flags):
            ctypes.cast(veto_ptr, ctypes.POINTER(w.DWORD)).contents.value = 4
            veto_name.value = "open file"
            return 23

        self.dlls["cfgmgr32"].functions["CM_Request_Device_EjectW"].callback = refused_eject
        result = w.eject_disk(disk_no)
        self.assertFalse(result.success)
        self.assertEqual((result.veto_type, result.veto_name, result.configret), (4, "open file", 23))

        self.dlls["setupapi"].functions["SetupDiEnumDeviceInterfaces"].callback = lambda *_args: 0
        self.last_error[0] = 259
        result = w.eject_disk(999)
        self.assertTrue(result.not_found)
        self.assertIsNone(result.configret)

    def test_notifications_sleep_and_known_folder(self):
        self.dlls["kernel32"] = StubDLL(CreateFileW=lambda *_args: 77, CloseHandle=lambda _h: 1,
                                       SetThreadExecutionState=lambda value: value)
        registrations = iter((101, 102, 103, 104))
        self.dlls["user32"] = StubDLL(
            RegisterDeviceNotificationW=lambda *_args: next(registrations),
            UnregisterDeviceNotification=lambda _notification: 1,
        )
        handles = w.register_volume_notifications(1, "\\\\?\\Volume{abc}\\")
        self.assertEqual(len(handles[0]), 4)
        w.unregister_device_notification(handles)
        self.assertTrue(w.inhibit_sleep(True))
        self.assertTrue(w.inhibit_sleep(False))

        folder = ctypes.create_unicode_buffer("C:\\Users\\tester\\AppData\\Roaming")
        shell = StubDLL()
        def get_known_folder(_guid, _flags, _token, out):
            ctypes.cast(out, ctypes.POINTER(ctypes.c_void_p)).contents.value = ctypes.addressof(folder)
            return 0

        shell.SHGetKnownFolderPath.callback = get_known_folder
        self.dlls["shell32"] = shell
        self.dlls["ole32"] = StubDLL(CoTaskMemFree=lambda _ptr: None)
        self.assertEqual(w.known_folder(w.FOLDERID_RoamingAppData, "APPDATA"), folder.value)
        self.assert_declared()

    def test_registration_failure_cleans_up_handle(self):
        self.dlls["kernel32"] = StubDLL(CreateFileW=lambda *_args: 77, CloseHandle=lambda _h: 1)
        self.dlls["user32"] = StubDLL(RegisterDeviceNotificationW=lambda *_args: 0,
                                      UnregisterDeviceNotification=lambda _notification: 1)
        self.last_error[0] = 5
        with self.assertRaises(OSError):
            w.register_volume_notifications(1, "\\\\?\\Volume{abc}\\")
        self.assertEqual(self.dlls["kernel32"].functions["CloseHandle"].calls, 1)
        self.assert_declared()

    def test_credential_roundtrip_and_delete_success(self):
        stored = w.CREDENTIAL()
        blob_owner = [None]
        freed = []

        def write(credential_ptr, _flags):
            source = ctypes.cast(credential_ptr, ctypes.POINTER(w.CREDENTIAL)).contents
            stored.Type = source.Type
            stored.TargetName = source.TargetName
            stored.CredentialBlobSize = source.CredentialBlobSize
            blob_owner[0] = (ctypes.c_ubyte * source.CredentialBlobSize).from_buffer_copy(
                ctypes.string_at(source.CredentialBlob, source.CredentialBlobSize))
            stored.CredentialBlob = ctypes.cast(blob_owner[0], ctypes.POINTER(ctypes.c_ubyte))
            stored.Persist = source.Persist
            return 1

        def read(_target, _kind, _flags, output):
            ctypes.cast(output, ctypes.POINTER(w.HANDLE)).contents.value = ctypes.addressof(stored)
            return 1

        self.dlls["advapi32"] = StubDLL(CredWriteW=write, CredReadW=read,
                                        CredDeleteW=lambda *_args: 1,
                                        CredFree=lambda ptr: freed.append(ptr.value))
        w.credential_write("fake-profile", "secret-файл")
        self.assertEqual(w.credential_read("fake-profile"), "secret-файл")
        w.credential_delete("fake-profile")
        self.assertEqual(len(freed), 1)
        self.assert_declared()

    def test_bitlocker_property_store_success(self):
        callback_type = ctypes.CFUNCTYPE
        release_type = callback_type(ctypes.c_ulong, w.HANDLE)
        getvalue_type = callback_type(ctypes.c_long, w.HANDLE, ctypes.POINTER(w.GUID), ctypes.POINTER(w.PROPVARIANT))

        @release_type
        def release(_this):
            return 1

        @getvalue_type
        def get_value(_this, _key, value):
            value.contents.vt = 19
            value.contents.value.uint64 = 6
            return 0

        vtable_type = ctypes.c_void_p * 6
        vtable = vtable_type()
        vtable[2] = ctypes.cast(release, ctypes.c_void_p).value
        vtable[5] = ctypes.cast(get_value, ctypes.c_void_p).value

        class PropertyStore(ctypes.Structure):
            _fields_ = [("vtable", ctypes.POINTER(ctypes.c_void_p))]

        holder = PropertyStore(ctypes.cast(vtable, ctypes.POINTER(ctypes.c_void_p)))
        store_pointer = ctypes.addressof(holder)

        def get_points(_path, buf, _size, needed):
            if buf is None:
                self.set_u32(needed, 5)
                self.last_error[0] = w.ERROR_MORE_DATA
                return 0
            for i, char in enumerate("C:\\\0\0"):
                buf[i] = char
            self.set_u32(needed, 5)
            return 1

        def get_store(_path, _bind, flags, _iid, result):
            self.assertEqual(flags, 0)
            ctypes.cast(result, ctypes.POINTER(w.HANDLE)).contents.value = store_pointer
            return 0

        def get_property_key(_name, key):
            ctypes.cast(key, ctypes.POINTER(w.GUID)).contents.Data1 = 77
            return 0

        self.dlls["kernel32"] = StubDLL(GetVolumePathNamesForVolumeNameW=get_points)
        self.dlls["ole32"] = StubDLL(CoInitializeEx=lambda *_args: 0, CoUninitialize=lambda: None)
        self.dlls["shell32"] = StubDLL(SHGetPropertyStoreFromParsingName=get_store)
        self.dlls["propsys"] = StubDLL(PSGetPropertyKeyFromName=get_property_key,
                                       PropVariantClear=lambda _value: 0)
        with mock.patch.object(w.ctypes, "WINFUNCTYPE", callback_type, create=True):
            self.assertEqual(w.bitlocker_raw("\\\\?\\Volume{abc}\\"), 6)
        self.assert_declared()

    def test_lock_path_and_run_registry_helpers(self):
        calls = []
        msvcrt = types.SimpleNamespace(LK_NBLCK=1, LK_UNLCK=2,
                                       locking=lambda fd, mode, count: calls.append(("lock", fd, mode, count)))
        with mock.patch.dict(sys.modules, {"msvcrt": msvcrt}), \
             mock.patch.object(w, "local_app_data", return_value="C:\\Data\\PlugBackup"), \
             mock.patch.object(w.os, "makedirs"), \
             mock.patch.object(w.os, "open", return_value=55) as open_file, \
             mock.patch.object(w.os, "fstat", return_value=types.SimpleNamespace(st_size=1)), \
             mock.patch.object(w.os, "lseek", return_value=0), \
             mock.patch.object(w.os, "close"):
            with mock.patch.object(w.os.path, "join", side_effect=lambda *parts: "\\".join(parts)):
                fd = w.lock_profile("profile")
                self.assertEqual(fd, 55)
                self.assertEqual(open_file.call_args[0][0], "C:\\Data\\PlugBackup\\run\\profile.lock")
                w.unlock_profile(fd)
        self.assertEqual([c[2] for c in calls], [1, 2])

        registry = {"values": {}}

        class Key:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        def create_key(_root, _path):
            return Key()

        def open_key(_root, _path, *_args):
            return Key()

        def set_value(_key, name, _reserved, _kind, value):
            registry["values"][name] = value

        def query_value(_key, name):
            if name not in registry["values"]:
                raise FileNotFoundError(name)
            return registry["values"][name], 1

        def delete_value(_key, name):
            del registry["values"][name]

        winreg = types.SimpleNamespace(HKEY_CURRENT_USER=1, KEY_SET_VALUE=2, REG_SZ=3,
                                       CreateKey=create_key, OpenKey=open_key, SetValueEx=set_value,
                                       QueryValueEx=query_value, DeleteValue=delete_value)
        with mock.patch.dict(sys.modules, {"winreg": winreg}):
            temp_name = "Temp_123"
            w.startup_set('"C:\\app.exe" --hidden', temp_name)
            self.assertEqual(w.startup_get(temp_name), '"C:\\app.exe" --hidden')
            w.startup_delete(temp_name)
            self.assertIsNone(w.startup_get(temp_name))
        self.assertEqual(w.startup_command("C:\\Program Files\\plug.exe"),
                         '"C:\\Program Files\\plug.exe" --hidden')

    def test_file_flush_wrapper(self):
        with mock.patch.object(w.os, "fsync") as fsync:
            self.assertIsNone(w.flush_file(19))
        fsync.assert_called_once_with(19)

    def test_property_store_and_credential_error_paths(self):
        points = StubDLL()
        points.GetVolumePathNamesForVolumeNameW.callback = lambda _path, buf, _size, needed: (
            self.set_u32(needed, 5) or 0 if buf is None else (setattr(buf, "value", "C:\\") or 1))
        self.dlls["kernel32"] = points
        # Ошибка PSGetPropertyKeyFromName проверяет ранний выход и очистку COM.
        self.dlls["ole32"] = StubDLL(CoInitializeEx=lambda *_args: 0, CoUninitialize=lambda: None)
        self.dlls["propsys"] = StubDLL(PSGetPropertyKeyFromName=lambda *_args: -1,
                                       PropVariantClear=lambda _value: 0)
        self.dlls["shell32"] = StubDLL()
        self.assertIsNone(w.bitlocker_raw("\\\\?\\Volume{abc}\\"))

        self.dlls["advapi32"] = StubDLL(
            CredWriteW=lambda *_args: 0,
            CredReadW=lambda *_args: 0,
            CredDeleteW=lambda *_args: 0,
            CredFree=lambda _ptr: None,
        )
        self.last_error[0] = 5
        with self.assertRaises(OSError):
            w.credential_write("profile", "secret")
        self.last_error[0] = 1168
        self.assertIsNone(w.credential_read("profile"))
        w.credential_delete("profile")
        self.assert_declared()


if __name__ == "__main__":
    unittest.main()
