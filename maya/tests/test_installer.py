"""Run with Python; installation checks use temporary user directories."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('fast64_installer', SOURCE / 'install.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallerTests(unittest.TestCase):
    def test_install_and_update(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'Maya user folder'
            target = installer.install(root, SOURCE)
            self.assertEqual((root / 'modules/Fast64Maya.mod').read_text(), installer.MODULE)
            for name in ['plug-ins/fast64Maya.py', 'scripts/fast64_maya/vendor/glb_export.py']:
                self.assertEqual((target / name).read_bytes(), (SOURCE / name).read_bytes())
            self.assertTrue((target / 'LICENSE.txt').exists())
            self.assertFalse(list(target.rglob('*.pyc')))
            installer.install(root, SOURCE)
            self.assertTrue((target / installer.MARKER).exists())

    def test_respects_configured_user_folder(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'MAYA_APP_DIR': temp}):
            self.assertEqual(installer.user_maya_folder(), Path(temp))

    def test_unmanaged_installation_is_protected(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / 'modules/Fast64Maya'
            target.mkdir(parents=True)
            existing = target / 'user-file.txt'
            existing.write_text('keep')
            with self.assertRaisesRegex(RuntimeError, 'unmanaged'):
                installer.install(temp, SOURCE)
            self.assertEqual(existing.read_text(), 'keep')
            self.assertFalse((target.parent / 'Fast64Maya.mod').exists())

    def test_legacy_registration_is_backed_up(self):
        with tempfile.TemporaryDirectory() as temp:
            modules = Path(temp) / 'modules'
            modules.mkdir()
            legacy = '+ Fast64Maya 0.1.0 /old/checkout/maya\n'
            (modules / 'Fast64Maya.mod').write_text(legacy)
            installer.install(temp, SOURCE)
            self.assertEqual((modules / 'Fast64Maya.mod.before-installer').read_text(), legacy)
            self.assertEqual((modules / 'Fast64Maya.mod').read_text(), installer.MODULE)

    def test_foreign_module_is_protected(self):
        with tempfile.TemporaryDirectory() as temp:
            modules = Path(temp) / 'modules'
            modules.mkdir()
            definition = modules / 'Fast64Maya.mod'
            definition.write_text('+ AnotherModule 1.0 /other\n')
            with self.assertRaisesRegex(RuntimeError, 'unrecognized'):
                installer.install(temp, SOURCE)
            self.assertEqual(definition.read_text(), '+ AnotherModule 1.0 /other\n')


if __name__ == '__main__':
    unittest.main()
