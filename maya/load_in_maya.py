"""Execute this saved file in Maya, or select it using the file picker."""
import os
import maya.cmds as cmds

filename = globals().get("__file__")
if not filename:
    chosen = cmds.fileDialog2(fileMode=1, fileFilter="Python (*.py)", caption="Select maya/load_in_maya.py")
    filename = chosen[0] if chosen else None
if filename:
    plugin = os.path.join(os.path.dirname(os.path.abspath(filename)), "plug-ins", "fast64Maya.py")
    if not os.path.isfile(plugin):
        raise ValueError("Select load_in_maya.py inside the Fast64 Maya directory")
    if not cmds.pluginInfo("fast64Maya.py", query=True, loaded=True):
        cmds.loadPlugin(plugin)
    cmds.fast64()
