"""Drag into Maya's viewport, or run with Python, to install Fast64 Maya."""
import argparse
import ctypes
import json
import os
from pathlib import Path
import sys
import tempfile

MODULE = '+ Fast64Maya 0.1.0 Fast64Maya\n'
MARKER = '.fast64-maya-install.json'


def user_maya_folder():
    """Respect Maya configuration and redirected Windows Documents folders."""
    if os.environ.get('MAYA_APP_DIR'):
        return Path(os.environ['MAYA_APP_DIR']).expanduser()
    try:
        import maya.cmds as cmds
        return Path(cmds.internalVar(userAppDir=True))
    except (ImportError, AttributeError, RuntimeError):
        pass
    if sys.platform == 'win32':
        documents = ctypes.create_unicode_buffer(32768)
        result = ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, documents)
        if result != 0:
            raise RuntimeError('Cannot locate Documents; supply --maya-app-dir')
        return Path(documents.value) / 'maya'
    if sys.platform == 'darwin':
        return Path.home() / 'Library/Preferences/Autodesk/maya'
    return Path.home() / 'maya'


def publish(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.fast64-', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
        os.replace(name, str(path))
    finally:
        if os.path.exists(name):
            os.unlink(name)


def install(maya_app_dir=None, source=None):
    source = Path(source or Path(__file__).resolve().parent)
    modules = Path(maya_app_dir or user_maya_folder()).expanduser().resolve() / 'modules'
    target = modules / 'Fast64Maya'
    definition = modules / 'Fast64Maya.mod'
    if target.exists() and not (target / MARKER).is_file():
        raise RuntimeError('An unmanaged Fast64Maya directory exists: ' + str(target))
    previous = definition.read_bytes() if definition.exists() else None
    if previous is not None and previous != MODULE.encode('utf-8'):
        lines = previous.decode('utf-8').splitlines()
        if len(lines) != 1 or not lines[0].startswith('+ Fast64Maya 0.1.0 '):
            raise RuntimeError('An unrecognized Fast64Maya.mod exists: ' + str(definition))
    files = {}
    for directory in ('plug-ins', 'scripts'):
        if not (source / directory).is_dir():
            raise RuntimeError('Keep install.py with the repository Maya folder: missing ' + directory)
        for file in (source / directory).rglob('*'):
            if file.is_file() and '__pycache__' not in file.parts and file.suffix != '.pyc':
                files[file.relative_to(source).as_posix()] = file.read_bytes()
    license_file = source.parent / 'LICENSE.txt'
    if not license_file.is_file():
        raise RuntimeError('Keep the repository LICENSE.txt with the installer')
    files['LICENSE.txt'] = license_file.read_bytes()
    assert 'plug-ins/fast64Maya.py' in files
    # Read and validate all inputs before changing an existing installation.
    if previous is not None and previous != MODULE.encode('utf-8'):
        backup = modules / 'Fast64Maya.mod.before-installer'
        if not backup.exists():
            publish(backup, previous)
    for relative, data in files.items():
        publish(target / relative, data)
    publish(target / MARKER, json.dumps({'module': 'Fast64Maya', 'files': sorted(files)}, indent=2).encode('utf-8'))
    publish(definition, MODULE.encode('utf-8'))
    print('Installed Fast64 Maya in ' + str(target))
    print('Restart Maya. In Windows > Settings/Preferences > Plug-in Manager, enable fast64Maya.py and Auto load.')
    return target


def onMayaDroppedPythonFile(*args):
    install()
    import maya.cmds as cmds
    cmds.confirmDialog(title='Fast64 Maya installed',
                       message='Restart Maya, then enable fast64Maya.py in Plug-in Manager.\nCheck Auto load to load it at startup.',
                       button=['OK'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--maya-app-dir', help='Maya user application folder; defaults to the current user')
    args = parser.parse_args()
    install(args.maya_app_dir)
