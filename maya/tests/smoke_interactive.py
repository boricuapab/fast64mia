"""Run inside interactive Maya. Uses disposable nodes and leaves the user's scene intact."""
import json
import os
import traceback
import tempfile
import maya.cmds as cmds

from fast64_maya import ui, scene, paint

REPORT = os.path.join(tempfile.gettempdir(), "fast64_maya_interactive_report.json")
original_selection = cmds.ls(selection=True, long=True) or []
original_context = cmds.currentCtx()
undo_state = cmds.undoInfo(query=True, state=True)
if not undo_state:
    cmds.undoInfo(stateWithoutFlush=True)
result = {}
cmds.undoInfo(openChunk=True, chunkName="Fast64 disposable smoke test")
try:
    mesh = cmds.polySphere(name="fast64SmokeMesh", radius=10, subdivisionsX=12, subdivisionsY=8,
                           constructionHistory=False)[0]
    shader = cmds.shadingNode("phong", asShader=True, skipSelect=True, name="fast64SmokePhong")
    sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name="fast64SmokeSG")
    cmds.connectAttr(shader + ".outColor", sg + ".surfaceShader")
    cmds.sets(mesh, edit=True, forceElement=sg)
    cmds.select(mesh)
    result["conversion"] = scene.convert_materials()
    paint.fill(rgb=(1, 1, 1), alpha=1)
    paint.use_for_export()
    context = paint.start(rgb=(0.2, 0.6, 1.0), alpha=0.75, radius=2, opacity=1)
    assert context and cmds.artAttrPaintVertexCtx(context, exists=True), "No native vertex paint context"
    result["context"] = context
    result["channel"] = cmds.artAttrPaintVertexCtx(context, query=True, paintChannel=True)
    result["color"] = cmds.artAttrPaintVertexCtx(context, query=True, colorRGBAValue=True)
    result["component"] = cmds.artAttrPaintVertexCtx(context, query=True, paintComponent=True)
    # Artisan's native flood uses the same paint context data as brush strokes.
    cmds.artAttrPaintVertexCtx(context, edit=True, clear=True)
    dag, fn = scene.mesh_fn(scene.selected_meshes()[0])
    c = fn.getFaceVertexColors()[0]
    assert abs(c.r-0.2) < 0.001 and abs(c.g-0.6) < 0.001 and abs(c.b-1) < 0.001 and abs(c.a-0.75) < 0.001, tuple(c)
    result["native_paint_color"] = (c.r, c.g, c.b, c.a)
    cmds.setToolTo(original_context)
    paint.start(channel="RGB", operation="Add")
    paint.start(channel="Alpha", operation="Smooth")
    result["ok"] = True
except Exception:
    result["ok"] = False
    result["error"] = traceback.format_exc()
finally:
    cmds.setToolTo(original_context)
    cmds.undoInfo(closeChunk=True)
    cmds.undo()
    cmds.select(original_selection, replace=True)
    if not undo_state:
        cmds.undoInfo(stateWithoutFlush=False)
    result["temporary_mesh_removed"] = not cmds.objExists("fast64SmokeMesh")
    ui.show()
    result["window"] = cmds.window(ui.WINDOW, exists=True)
    result["menu"] = cmds.menu(ui.MENU, exists=True)
    with open(REPORT, "w") as stream:
        json.dump(result, stream, indent=2)
    print("Fast64 interactive smoke:", json.dumps(result))
