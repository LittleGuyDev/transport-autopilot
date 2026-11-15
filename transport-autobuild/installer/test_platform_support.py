import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest import mock

import platform_support as platform
import registration


class WindowsProcessTest(unittest.TestCase):
    @staticmethod
    def system_directory_api(directory=r'C:\Windows\System32', result=None):
        def fill(buffer, capacity):
            buffer.value = directory
            return len(directory) if result is None else result
        api = mock.Mock(side_effect=fill)
        return mock.Mock(GetSystemDirectoryW=api), api

    def test_uses_typed_windows_system_directory_api(self):
        library, api = self.system_directory_api(r'D:\Windows OS\System32')
        with mock.patch.object(platform.ctypes, 'WinDLL', create=True, return_value=library) as load:
            self.assertEqual(platform._windows_tasklist(), r'D:\Windows OS\System32\tasklist.exe')
        load.assert_called_once_with('kernel32', use_last_error=True)
        self.assertEqual(api.argtypes, [platform.ctypes.POINTER(platform.ctypes.c_wchar), platform.ctypes.c_uint])
        self.assertIs(api.restype, platform.ctypes.c_uint)
        self.assertEqual(api.call_args[0][1], 32768)

    def test_invalid_system_directory_fails_before_any_process_is_started(self):
        cases = [('', 0), ('', 1), (r'C:\Windows\System32', 32768),
                 (r'C:\Windows\System32', 32769), ('relative', None),
                 (r'C:Windows\System32', None), (r'\Windows\System32', None),
                 ('C:\\Windows\0ignored', None), (r'C:\Windows\System32', 4)]
        for directory, result in cases:
            with self.subTest(directory=directory, result=result):
                library, _ = self.system_directory_api(directory, result)
                with mock.patch.object(platform.sys, 'platform', 'win32'), \
                        mock.patch.object(platform.ctypes, 'WinDLL', create=True, return_value=library), \
                        mock.patch.object(platform.subprocess, 'run') as run:
                    with self.assertRaises(OSError):
                        platform.game_running(Path('unused'))
                    run.assert_not_called()

    def test_windows_api_errors_fail_closed(self):
        library, api = self.system_directory_api()
        api.side_effect = OSError('native API error')
        for options in ({'side_effect': OSError('cannot load kernel32')}, {'return_value': library}):
            with self.subTest(options=options):
                with mock.patch.object(platform.sys, 'platform', 'win32'), \
                        mock.patch.object(platform.ctypes, 'WinDLL', create=True, **options), \
                        mock.patch.object(platform.subprocess, 'run') as run:
                    with self.assertRaises(OSError):
                        platform.game_running(Path('unused'))
                    run.assert_not_called()

    def test_tasklist_ignores_hostile_cwd_path_and_windows_environment(self):
        library, _ = self.system_directory_api()
        previous_directory = os.getcwd()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'tasklist.exe').write_bytes(b'hostile fixture; must never execute')
            try:
                os.chdir(temporary)
                with mock.patch.dict(os.environ, {'PATH': temporary, 'SystemRoot': temporary, 'WINDIR': temporary}), \
                        mock.patch.object(platform.sys, 'platform', 'win32'), \
                        mock.patch.object(platform.ctypes, 'WinDLL', create=True, return_value=library), \
                        mock.patch.object(platform.subprocess, 'run', return_value=mock.Mock(stdout='')) as run:
                    self.assertFalse(platform.game_running(root / 'app.asar'))
                    run.assert_called_once_with([r'C:\Windows\System32\tasklist.exe', '/FO', 'CSV', '/NH'],
                                                capture_output=True, text=True, check=True)
            finally:
                os.chdir(previous_directory)

    def test_process_csv_matching_and_inspection_failure_are_preserved(self):
        for output, expected in [('"Other, Game.exe","123","Console"\r\n', False),
                                 ('"GEOPLANETICAL.exe","456","Console"\r\n', True),
                                 ('\r\n"Geoplanetical Helper.exe","456"\r\n', True)]:
            with self.subTest(output=output):
                with mock.patch.object(platform.sys, 'platform', 'win32'), \
                        mock.patch.object(platform, '_windows_tasklist', return_value=r'C:\Windows\System32\tasklist.exe'), \
                        mock.patch.object(platform.subprocess, 'run', return_value=mock.Mock(stdout=output)):
                    self.assertEqual(platform.game_running(Path('unused')), expected)
        failure = subprocess.CalledProcessError(1, 'tasklist.exe')
        with mock.patch.object(platform.sys, 'platform', 'win32'), \
                mock.patch.object(platform, '_windows_tasklist', return_value=r'C:\Windows\System32\tasklist.exe'), \
                mock.patch.object(platform.subprocess, 'run', side_effect=failure):
            with self.assertRaises(subprocess.CalledProcessError):
                platform.game_running(Path('unused'))


class PlatformTest(unittest.TestCase):
    def test_normalization_preserves_symlink_for_safety_check(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            game = root / 'real/resources'
            game.mkdir(parents=True)
            (game / 'app.asar').write_bytes(b'fixture')
            alias = root / 'alias'
            try: alias.symlink_to(game.parent, target_is_directory=True)
            except OSError: self.skipTest('Symlinks unavailable')
            path = platform.normalize_archive(alias)
            self.assertEqual(path, alias / 'resources/app.asar')
            with self.assertRaises(ValueError): registration._safe(path)

    def test_pe_updates_only_named_integrity_resource(self):
        old,new = 'a'*64,'b'*64
        data=bytearray(2048);data[:2]=b'MZ';struct.pack_into('<I',data,0x3c,0x80)
        data[0x80:0x84]=b'PE\0\0';struct.pack_into('<H',data,0x86,1);struct.pack_into('<H',data,0x94,240)
        optional=0x98;struct.pack_into('<H',data,optional,0x20b)
        struct.pack_into('<II',data,optional+128,0x1000,1024)
        struct.pack_into('<IIII',data,optional+248,1024,0x1000,1024,512)
        base=512
        for offset in (0,24,48): struct.pack_into('<HH',data,base+offset+12,offset!=48,offset==48)
        struct.pack_into('<II',data,base+16,0x80000000|100,0x80000000|24)
        struct.pack_into('<II',data,base+40,0x80000000|130,0x80000000|48)
        struct.pack_into('<II',data,base+64,1033,80)
        for offset,label in ((100,'INTEGRITY'),(130,'ELECTRONASAR')):
            encoded=label.encode('utf-16le');struct.pack_into('<H',data,base+offset,len(label));data[base+offset+2:base+offset+2+len(encoded)]=encoded
        payload=json.dumps([{'file':'resources\\app.asar','alg':'sha256','value':old}]).encode()
        struct.pack_into('<IIII',data,base+80,0x1000+200,len(payload),0,0)
        data[base+200:base+200+len(payload)]=payload
        data[1600:1664]=old.encode()  # unrelated matching bytes must remain untouched
        updated=platform.update_windows_integrity(data,old,new)
        self.assertEqual(updated[1600:1664],old.encode())
        self.assertEqual(updated.count(new.encode()),1)
        self.assertEqual(sum(a!=b for a,b in zip(data,updated)),64)
        with self.assertRaises(ValueError): platform.update_windows_integrity(data,'c'*64,new)


if __name__=='__main__':unittest.main()
