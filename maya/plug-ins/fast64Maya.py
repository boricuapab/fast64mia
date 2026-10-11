"""Fast64 native Maya 2023+ plugin entrypoint, independent of Blender."""
import json
import os
import sys
import maya.api.OpenMaya as om
import maya.cmds as cmds
import maya.utils

# Maya's Python plugin loader does not always populate __file__.
PLUGIN_FILE = globals().get("__file__", sys._getframe().f_code.co_filename)
SCRIPTS = os.path.abspath(os.path.join(os.path.dirname(PLUGIN_FILE), "..", "scripts"))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)


def maya_useNewAPI():
    pass


class Fast64Command(om.MPxCommand):
    @staticmethod
    def creator():
        return Fast64Command()

    @staticmethod
    def syntax():
        syntax = om.MSyntax()
        syntax.addFlag("-cv", "-convert")
        syntax.addFlag("-v", "-validate")
        syntax.addFlag("-p", "-paint")
        syntax.addFlag("-e", "-export", om.MSyntax.kString)
        syntax.addFlag("-n", "-name", om.MSyntax.kString)
        syntax.addFlag("-s", "-scale", om.MSyntax.kDouble)
        syntax.addFlag("-m", "-microcode", om.MSyntax.kString)
        syntax.addFlag("-c", "-collision")
        return syntax

    def doIt(self, arguments):
        from fast64_maya import scene, paint, core, ui
        args = om.MArgDatabase(self.syntax(), arguments)
        scale = args.flagArgumentDouble("-scale", 0) if args.isFlagSet("-scale") else 1.0
        microcode = args.flagArgumentString("-microcode", 0) if args.isFlagSet("-microcode") else "F3DEX2"
        if args.isFlagSet("-convert"):
            result = scene.convert_materials()
        elif args.isFlagSet("-paint"):
            result = paint.start()
        elif args.isFlagSet("-export"):
            result = scene.export(args.flagArgumentString("-export", 0),
                                  args.flagArgumentString("-name", 0) if args.isFlagSet("-name") else "model",
                                  scale, microcode, args.isFlagSet("-collision"))
        elif args.isFlagSet("-validate"):
            groups = scene.gather(scale, args.isFlagSet("-collision"))
            result = core.collision("validation", groups)[2] if args.isFlagSet("-collision") else core.display_list("validation", groups, microcode)[2]
        else:
            if cmds.about(batch=True):
                raise RuntimeError("Fast64 UI requires interactive Maya; use -validate, -convert, or -export")
            result = ui.show()
        self.setResult(json.dumps(result))


def initializePlugin(plugin):
    fn = om.MFnPlugin(plugin, "fast64mia contributors", "0.1.0", "Any")
    fn.registerCommand("fast64", Fast64Command.creator, Fast64Command.syntax)
    from fast64_maya import ui
    try:
        if not cmds.about(batch=True):
            # Autoload can run before Maya creates its main window.
            maya.utils.executeDeferred(_install_menu_if_loaded)
    except Exception:
        fn.deregisterCommand("fast64")
        raise


def _install_menu_if_loaded():
    # A user may unload the plugin before the deferred callback runs.
    if cmds.pluginInfo("fast64Maya.py", query=True, loaded=True):
        from fast64_maya import ui
        ui.install_menu()


def uninitializePlugin(plugin):
    from fast64_maya import ui
    ui.remove_ui()
    om.MFnPlugin(plugin).deregisterCommand("fast64")
