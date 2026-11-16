"""Isolated pre-launch entry point; no game process starts after a failed gate."""
from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path
import plistlib
import stat
import subprocess
import sys

HERE = Path(__file__).absolute().parent
ERROR_TITLE = 'Automatic update stopped'
MAX_ERROR_BYTES = 8192
REPAIR_INSTRUCTIONS = (
    'Close Geoplanetical and let Steam finish any Workshop download, then try Play again. '
    'If it still stops, run the current complete mod installer to repair setup and copy its Steam launch line again. '
    'Keep the selected Python installation available. Share last-error.txt when asking for help.'
)


def _setup():
    path = HERE / 'update_setup.py'
    if not path.is_file():
        path = HERE / 'setup.py'
    for ancestor in (path,) + tuple(path.parents):
        info = ancestor.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Trusted launcher cannot use symlink or reparse paths')
    spec = importlib.util.spec_from_file_location('_lgd_launcher_setup', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    previous_bytecode = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous_bytecode
    return module


def _selected_executable(engine, archive, allow_recovery=False):
    archive = engine._absolute(archive)
    if archive.name != 'app.asar' or not archive.is_file() and not allow_recovery:
        raise ValueError('The enrolled game archive is missing or invalid')
    if archive.parent.name == 'Resources' and archive.parent.parent.name == 'Contents':
        app = archive.parent.parent.parent
        plist = engine._absolute(app / 'Contents/Info.plist')
        if plist.exists():
            info = plistlib.loads(engine._read(plist, 4 * 1024 * 1024))
            name = info.get('CFBundleExecutable')
        elif allow_recovery:
            # The original installer can recover a bundle interrupted between
            # rename steps. Its signed worker must validate the restored plist
            # and executable before on_ready permits any launch. Until then,
            # use only the known game's lexical executable path.
            name = 'Geoplanetical'
        else:
            raise ValueError('The enrolled native macOS bundle metadata is missing')
        if not isinstance(name, str) or name in ('', '.', '..') or Path(name).name != name or '\\' in name:
            raise ValueError('The enrolled native macOS executable is invalid')
        executable = engine._absolute(app / 'Contents/MacOS' / name)
    else:
        executable = engine._absolute(archive.parent.parent / 'Geoplanetical.exe')
    if not executable.is_file() and not allow_recovery:
        raise ValueError('The enrolled game executable is missing')
    return executable


def _steam():
    """Open this fixed app ID using an absolute OS utility or Steam client."""
    if sys.platform == 'darwin':
        return subprocess.Popen(['/usr/bin/open', 'steam://rungameid/940230'], shell=False)
    if os.name == 'nt':
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam') as key:
                executable = winreg.QueryValueEx(key, 'SteamExe')[0]
        except OSError as exc:
            raise ValueError('Open Steam once before using the Play shortcut') from exc
        setup = _setup()
        executable = setup._absolute(executable)
        if executable.name.lower() != 'steam.exe' or not executable.is_file():
            raise ValueError('The registered Steam client is invalid')
        return subprocess.Popen([str(executable), '-applaunch', '940230'], shell=False)
    raise ValueError('Automatic launching supports Windows and native macOS only')


def launch(config_path, original_args=None, *, public_key=None, popen=None,
           steam_opener=None, updater=None):
    """Check all enrolled mods, then launch the exact selected game argument list.

    The Play shortcut has no original argument list: it prepares updates and
    opens Steam, which applies the documented launch option. Steam mode always
    launches its original game executable directly and can never recurse to URI.
    Dependency injection is available to isolated tests, never via CLI options.
    """
    setup = _setup()
    engine = setup._engine()
    config_path = setup._absolute(config_path)
    state = setup._private(config_path.parent)
    if config_path.name != 'config.json':
        raise ValueError('Launcher requires its enrolled config.json')
    config = setup._launcher_config(engine, config_path, HERE)
    runtime = setup._absolute(config['runtime'])
    if runtime != HERE or config_path.parent != HERE.parent or setup._runtime_path(state, runtime) != HERE:
        raise ValueError('Registration does not belong to this trusted launcher runtime')
    setup._private(runtime)
    key = setup._load_key(runtime) if public_key is None else public_key
    target = _selected_executable(engine, config['game'], allow_recovery=bool(config['products']))
    argv = None
    if original_args is not None:
        argv = list(original_args)
        if not argv or any(not isinstance(part, str) or '\x00' in part for part in argv):
            raise ValueError('Steam must supply the original game executable and arguments')
        if engine._absolute(argv[0]) != target:
            raise ValueError('Steam selected a different executable than the enrolled game')
    process = popen or subprocess.Popen
    opened = []

    def enrollment_unchanged():
        if setup._launcher_config(engine, config_path, HERE) != config:
            raise ValueError('Mod enrollment changed; launch again to use its current registration')

    def ready(result):
        if result.executable != target:
            raise ValueError('The updater selected a different game executable')
        # Re-read native metadata after a recovery may have restored the bundle;
        # existence is mandatory here even though preflight allowed it missing.
        if _selected_executable(engine, config['game']) != target:
            raise ValueError('The recovered game executable does not match enrollment')
        if argv is not None:
            opened.append(process(argv, shell=False))

    if config['products']:
        (updater or engine.run_updates)(state, config['products'], config['game'], config['profile'],
                                        public_key=key, python=config['python'], on_ready=ready,
                                        before_updates=enrollment_unchanged)
    else:
        # Unregistering the final mod leaves existing Steam launch options usable.
        # No package is loaded and a removed product is never silently re-enrolled.
        with engine.exclusive(state):
            enrollment_unchanged()
            if argv is not None:
                opened.append(process(argv, shell=False))
    if argv is None:
        # Let the coordinator release its lock before Steam invokes this launcher
        # again with --steam. The second launch repeats every safety check.
        return (steam_opener or _steam)()
    return opened[0] if opened else None


def _clean_reason(error, limit):
    import unicodedata
    value = str(error)[:4096]
    value = ''.join(' ' if unicodedata.category(character).startswith('C') else character for character in value)
    value = ' '.join(value.split()) or 'The update could not be validated or installed.'
    encoded = value.encode('utf-8', errors='replace')
    return encoded[:limit].decode('utf-8', errors='ignore') + ('...' if len(encoded) > limit else '')


def _error_log(config_path, reason):
    """Write only inside this launcher's independently validated private state.

    Exception text and paths inside the exception never select an output path.
    Invalid/foreign config paths deliberately receive no persistent write.
    """
    setup = _setup()
    path = setup._absolute(config_path)
    if path.name != 'config.json' or path.parent != HERE.parent:
        raise ValueError('Error logging requires this runtime\'s enrolled config.json')
    state = setup._private(path.parent)
    setup._runtime_path(state, HERE)
    setup._private(HERE)
    engine = setup._engine()
    config = setup._config(engine, path)
    if setup._absolute(config['runtime']) != HERE:
        raise ValueError('Error logging cannot use a foreign runtime registration')
    target = setup._safe(state / 'last-error.txt')
    if target.exists() and not target.is_file():
        raise ValueError('Error log destination is not a regular file')
    text = ERROR_TITLE + '\n\nReason: ' + reason + '\n\n' + REPAIR_INSTRUCTIONS
    data = text.encode('utf-8')[:MAX_ERROR_BYTES - 1].decode('utf-8', errors='ignore').encode('utf-8') + b'\n'
    temporary = setup._safe(state / ('.last-error-' + os.urandom(12).hex() + '.tmp'))
    try:
        descriptor = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(0o600)
        setup._safe(target)
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return target


def _notify_failure(title, message):
    """Native local UI only; all variable text travels as data, never code."""
    if sys.platform == 'darwin':
        script = ('on run argv\n'
                  'display dialog (item 2 of argv) with title (item 1 of argv) '
                  'buttons {"OK"} default button "OK" with icon caution giving up after 60\n'
                  'end run')
        subprocess.Popen(['/usr/bin/osascript', '-e', script, '--', title, message],
                         shell=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL('user32', use_last_error=True)
        show = user32.MessageBoxW
        show.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
        show.restype = ctypes.c_int
        if not show(None, message, title, 0x00050030):
            raise ctypes.WinError(ctypes.get_last_error())


def _report_failure(config_path, error, *, notifier=None, interactive=None):
    short_reason = _clean_reason(error, 600)
    try:
        path = _error_log(config_path, _clean_reason(error, 3000))
        details = 'Error log: ' + str(path)
    except Exception:
        details = 'A private error log could not be written because the updater registration could not be validated or saved.'
    message = 'Reason: ' + short_reason + '\n\n' + details + '\n\n' + REPAIR_INSTRUCTIONS
    try:
        if sys.stderr is not None:
            print(ERROR_TITLE + '\n' + message, file=sys.stderr)
    except (OSError, ValueError):
        pass
    if interactive is None:
        try:
            interactive = bool(sys.stderr is not None and sys.stderr.isatty())
        except (OSError, ValueError):
            interactive = False
    if not interactive:
        try:
            (notifier or _notify_failure)(ERROR_TITLE, message)
        except Exception:
            # The persistent log and stderr remain useful when native UI is
            # unavailable. Notification failure never resumes a game launch.
            try:
                if sys.stderr is not None:
                    print('The error dialog could not be displayed. Use the details above to repair setup.', file=sys.stderr)
            except (OSError, ValueError):
                pass


def main(argv=None, *, notifier=None, interactive=None):
    class LaunchParser(argparse.ArgumentParser):
        config_path = None

        def error(self, message):
            _report_failure(self.config_path, ValueError('Invalid launch options: ' + message),
                            notifier=notifier, interactive=interactive)
            raise SystemExit(2)

    parser = LaunchParser(description='Update Geoplanetical mods before launching')
    parser.add_argument('--config', required=True)
    parser.add_argument('--steam', action='store_true')
    parser.add_argument('original', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    parser.config_path = args.config
    original = args.original
    if original and original[0] == '--':
        original = original[1:]
    if original and not args.steam:
        parser.error('Original game arguments require --steam')
    if args.steam and not original:
        parser.error('--steam requires the original game executable after --')
    try:
        launch(args.config, original if args.steam else None)
    except Exception as exc:
        _report_failure(args.config, exc, notifier=notifier, interactive=interactive)
        return 1
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print('Automatic updates stopped the game launch: ' + str(exc), file=sys.stderr)
        raise SystemExit(1)
