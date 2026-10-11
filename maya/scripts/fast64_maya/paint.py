"""Native Maya vertex-color display, fill, and Artisan brush tools."""
import maya.cmds as cmds
import maya.mel as mel
import maya.api.OpenMaya as om

from . import scene


def ensure_colors():
    meshes = scene.selected_meshes()
    for mesh in meshes:
        sets = cmds.polyColorSet(mesh, query=True, allColorSets=True) or []
        if not sets:
            cmds.polyColorSet(mesh, create=True, colorSet="Col", representation="RGBA", clamped=True)
            cmds.polyColorSet(mesh, currentColorSet=True, colorSet="Col")
            cmds.polyColorPerVertex(mesh, rgb=(1, 1, 1), alpha=1, colorDisplayOption=True)
        else:
            current = cmds.polyColorSet(mesh, query=True, currentColorSet=True) or []
            if not current:
                cmds.polyColorSet(mesh, currentColorSet=True, colorSet=sets[0])
    return meshes


def display(enabled=True, alpha=False):
    meshes = ensure_colors() if enabled else scene.selected_meshes()
    cmds.polyOptions(meshes, colorShadedDisplay=enabled, colorMaterialChannel="ambientDiffuse",
                     materialBlend="overwrite", displayAlphaAsGreyScale=alpha)
    for panel in cmds.getPanel(type="modelPanel") or []:
        cmds.modelEditor(panel, edit=True, displayAppearance="smoothShaded")
    return meshes


def fill(rgb=(1, 1, 1), alpha=1):
    selection = cmds.ls(selection=True, long=True) or []
    cmds.undoInfo(openChunk=True, chunkName="Fast64 fill vertex colors")
    try:
        ensure_colors()
        components = cmds.polyListComponentConversion(selection, toVertexFace=True)
        if not components:
            raise ValueError("Select polygon meshes, faces, or vertices to fill")
        cmds.polyColorPerVertex(components, rgb=rgb, alpha=alpha, colorDisplayOption=True)
        display()
    finally:
        cmds.undoInfo(closeChunk=True)


def start(rgb=(1, 1, 1), alpha=1, radius=1, opacity=1, operation="Replace", channel="RGBA", face_vertices=True):
    if cmds.about(batch=True):
        raise RuntimeError("Vertex brush painting requires interactive Maya; use fill() for batch color edits")
    cmds.undoInfo(openChunk=True, chunkName="Fast64 initialize vertex paint")
    try:
        display()
    finally:
        cmds.undoInfo(closeChunk=True)
    mel.eval("PaintVertexColorTool;")
    context = cmds.currentCtx()
    cmds.artAttrPaintVertexCtx(context, edit=True, colorRGBAValue=tuple(rgb) + (alpha,),
                             radius=radius, opacity=opacity, selectedattroper=operation.lower(),
                             paintRGBA=channel == "RGBA", paintChannel="A" if channel == "Alpha" else channel,
                             paintNumChannels=4 if channel == "RGBA" else (1 if channel == "Alpha" else 3),
                             paintComponent=2 if face_vertices else 1, disablelighting=True)
    return context


def use_for_export():
    changed = {}
    for mesh in scene.selected_meshes():
        dag, fn = scene.mesh_fn(mesh)
        groups, _ = fn.getConnectedShaders(dag.instanceNumber())
        for group in groups:
            shader = scene.shader_for_group(om.MFnDependencyNode(group).name())
            if not scene.attr(shader, "fast64Material", False):
                raise ValueError("Convert Maya materials to F3D before enabling vertex-color export")
            mode = scene.MODES[scene.attr(shader, "fast64Mode", 3)]
            changed[shader] = "TextureVertexColor" if mode in ("TextureShaded", "TextureVertexColor") else "VertexColor"
    cmds.undoInfo(openChunk=True, chunkName="Fast64 enable vertex-color materials")
    try:
        ensure_colors()
        for shader, target in changed.items():
            cmds.setAttr(shader + ".fast64Mode", scene.MODES.index(target))
    finally:
        cmds.undoInfo(closeChunk=True)
    return sorted(changed)
