"""Fast64's native Maya interface."""
import json
import maya.cmds as cmds
import maya.mel as mel

from . import scene, paint, core, attributes

WINDOW = "fast64MayaWindow"
MENU = "fast64MayaMenu"
CONTROLS = {}


def safely(function, *args, **kwargs):
    try:
        result = function(*args, **kwargs)
        if cmds.scrollField("fast64MayaStatus", exists=True):
            summary=''
            if isinstance(result,dict) and 'textureQC' in result:
                lines=['Texture / UV export QC:']
                for item in result['textureQC']:
                    lines.append('{}: {} — {} -> {}'.format(item['material'],item['reason'],item['sourceSize'],item['exportSize']))
                    if item['cropped']:
                        for mesh in item['affectedMeshes']:
                            lines.append('  Adjusted {} / {}: {} faces'.format(mesh['mesh'],mesh['uvSet'],len(mesh['faces'])))
                lines.append('Full report: '+result.get('textureQCReport',''))
                summary='\n'.join(lines)+'\n\n'
            cmds.scrollField("fast64MayaStatus", edit=True, text=summary+json.dumps(result, indent=2, default=str))
        return result
    except Exception as error:
        cmds.warning("Fast64: " + str(error))
        if cmds.scrollField("fast64MayaStatus", exists=True):
            cmds.scrollField("fast64MayaStatus", edit=True, text=str(error))
        return None


def browse(*_):
    result = cmds.fileDialog2(fileMode=3, caption="Fast64 export directory")
    if result:
        cmds.textFieldButtonGrp(CONTROLS["directory"], edit=True, text=result[0])
        cmds.optionVar(stringValue=("fast64MayaExportDirectory", result[0]))


def export(collision=False):
    directory = cmds.textFieldButtonGrp(CONTROLS["directory"], query=True, text=True)
    if not directory:
        raise ValueError("Choose an export directory")
    return scene.export(directory, cmds.textFieldGrp(CONTROLS["name"], query=True, text=True),
                        cmds.floatFieldGrp(CONTROLS["scale"], query=True, value1=True),
                        cmds.optionMenuGrp(CONTROLS["microcode"], query=True, value=True), collision)


def validate():
    groups = scene.gather(cmds.floatFieldGrp(CONTROLS["scale"], query=True, value1=True))
    _, _, stats = core.display_list("validation", groups,
                                    cmds.optionMenuGrp(CONTROLS["microcode"], query=True, value=True))
    stats["materials"] = len(groups)
    stats["result"] = "Native export validation passed"
    return stats


def paint_values():
    return {"rgb": cmds.colorSliderGrp(CONTROLS["color"], query=True, rgbValue=True),
            "alpha": cmds.floatSliderGrp(CONTROLS["alpha"], query=True, value=True)}


def brush():
    return paint.start(**paint_values(), radius=cmds.floatSliderGrp(CONTROLS["radius"], query=True, value=True),
                       opacity=cmds.floatSliderGrp(CONTROLS["opacity"], query=True, value=True),
                       operation=cmds.optionMenuGrp(CONTROLS["operation"], query=True, value=True),
                       channel=cmds.optionMenuGrp(CONTROLS["channel"], query=True, value=True),
                       face_vertices=cmds.checkBox(CONTROLS["face_vertices"], query=True, value=True))


def update_brush(*_):
    context = cmds.currentCtx()
    if cmds.artAttrPaintVertexCtx(context, exists=True):
        safely(brush)


def show(*_):
    if cmds.window(WINDOW, exists=True):
        cmds.deleteUI(WINDOW)
    window = cmds.window(WINDOW, title="Fast64 Maya — Native N64 Tools", widthHeight=(510, 760), sizeable=True)
    cmds.scrollLayout(childResizable=True)
    cmds.columnLayout(adjustableColumn=True, rowSpacing=7)
    cmds.text(label="Native Maya port • F3D / F3DEX2 • Static meshes and SM64 collision", align="left")
    cmds.text(label="Game scenes, rigs, animation, ROM import, CI textures, and custom combiners are not ported.", align="left")
    cmds.frameLayout(label="F3D Materials", collapsable=True, marginWidth=8, marginHeight=7)
    cmds.columnLayout(adjustableColumn=True, rowSpacing=5)
    cmds.button(label="Convert Maya Materials to F3D (selected meshes)", command=lambda *_: safely(scene.convert_materials))
    cmds.button(label="Create and Assign F3D Material", command=lambda *_: safely(scene.create_material))
    cmds.button(label="Verify Actual Face Material Assignments", command=lambda *_: safely(scene.verify_material_assignments))
    CONTROLS['resize'] = cmds.checkBox(label='Automatically resize exported textures to fit N64 TMEM',value=True)
    CONTROLS['autocrop'] = cmds.checkBox(label='Automatically crop sparse decals / small UV regions (clamped textures)',value=True)
    CONTROLS['texbits'] = cmds.optionMenuGrp(label='Texture format')
    for label in ('RGBA32 (smooth alpha)', 'RGBA16 (one-bit alpha)'): cmds.menuItem(label=label)
    CONTROLS['texmax'] = cmds.optionMenuGrp(label='Maximum texture dimension')
    for label in ('32','64','128','256'): cmds.menuItem(label=label)
    CONTROLS['decal'] = cmds.checkBox(label='Alpha texture over vertex colors (opaque surface)',value=False)
    cmds.button(label='Apply Texture Options to Selected Mesh Materials',command=lambda *_: safely(texture_options))
    cmds.text(label="Lambert / Blinn / Phong / Phong E / Standard Surface. Diffuse + file textures + opacity.", align="left")
    cmds.text(label="Edit material Extra Attributes for F3D mode, render layer, texture, UVs, and collision surface.", align="left")
    cmds.setParent("..")
    cmds.setParent("..")
    cmds.frameLayout(label="Vertex Color Paint", collapsable=True, marginWidth=8, marginHeight=7)
    cmds.columnLayout(adjustableColumn=True, rowSpacing=5)
    CONTROLS["color"] = cmds.colorSliderGrp(label="Paint color", rgbValue=(1, 1, 1), changeCommand=update_brush)
    CONTROLS["alpha"] = cmds.floatSliderGrp(label="Alpha", field=True, minValue=0, maxValue=1, value=1, changeCommand=update_brush)
    CONTROLS["radius"] = cmds.floatSliderGrp(label="Brush radius", field=True, minValue=0.01, maxValue=50, fieldMaxValue=10000, value=1, changeCommand=update_brush)
    CONTROLS["opacity"] = cmds.floatSliderGrp(label="Brush opacity", field=True, minValue=0, maxValue=1, value=1, changeCommand=update_brush)
    CONTROLS["operation"] = cmds.optionMenuGrp(label="Operation", changeCommand=update_brush)
    for label in ("Replace", "Add", "Scale", "Smooth"):
        cmds.menuItem(label=label)
    CONTROLS["channel"] = cmds.optionMenuGrp(label="Channels", changeCommand=update_brush)
    for label in ("RGBA", "RGB", "Alpha"):
        cmds.menuItem(label=label)
    CONTROLS["face_vertices"] = cmds.checkBox(label="Paint face vertices (preserve color seams)", value=True, changeCommand=update_brush)
    cmds.button(label="Paint Vertex Colors", command=lambda *_: safely(brush))
    cmds.button(label="Fill Selected Meshes / Vertices / Faces", command=lambda *_: safely(paint.fill, **paint_values()))
    cmds.rowLayout(numberOfColumns=3, adjustableColumn=1)
    cmds.button(label="Show Colors", command=lambda *_: safely(paint.display, True))
    cmds.button(label="Show Alpha", command=lambda *_: safely(paint.display, True, True))
    cmds.button(label="Hide Colors", command=lambda *_: safely(paint.display, False))
    cmds.setParent("..")
    cmds.button(label="Use Painted Vertex Colors in F3D Export", command=lambda *_: safely(paint.use_for_export))
    cmds.button(label="Prepare Col + Alpha Color Attributes and UVMap", command=lambda *_: safely(attributes.prepare))
    cmds.text(label="Uses the mesh's current RGBA color set; creates a white Col set when none exists.", align="left")
    cmds.setParent("..")
    cmds.setParent("..")
    cmds.frameLayout(label="Native Export", collapsable=True, marginWidth=8, marginHeight=7)
    cmds.columnLayout(adjustableColumn=True, rowSpacing=5)
    CONTROLS["name"] = cmds.textFieldGrp(label="C symbol / asset name", text="model")
    directory = cmds.optionVar(query="fast64MayaExportDirectory") if cmds.optionVar(exists="fast64MayaExportDirectory") else ""
    CONTROLS["directory"] = cmds.textFieldButtonGrp(label="Export directory", text=directory, buttonLabel="Browse", buttonCommand=browse)
    CONTROLS["scale"] = cmds.floatFieldGrp(label="N64 units per cm", numberOfFields=1, value1=1)
    CONTROLS["microcode"] = cmds.optionMenuGrp(label="Microcode")
    cmds.menuItem(label="F3DEX2")
    cmds.menuItem(label="F3D")
    cmds.button(label="Validate Selected Meshes and Materials", command=lambda *_: safely(validate))
    cmds.button(label="Export F3D Display List C", command=lambda *_: safely(export))
    cmds.button(label="Export Static SM64 Collision C", command=lambda *_: safely(export, True))
    cmds.button(label="Animation Clips…", command=lambda *_: safely(show_animation_clips))
    cmds.button(label="Export Selected F3D GLB…", command=lambda *_: safely(export_glb))
    cmds.text(label="World-space evaluated meshes. Textures: power-of-two RGBA16, maximum 4 KiB.", align="left")
    cmds.text(label="Exports to directory/asset_name; existing outputs are protected.", align="left")
    cmds.setParent("..")
    cmds.setParent("..")
    cmds.scrollField("fast64MayaStatus", editable=False, wordWrap=True, height=100,
                     text="Select meshes, convert their materials, then paint or export. No Blender required.")
    cmds.showWindow(window)
    return window


def show_animation_clips():
    from . import animation_clips
    return animation_clips.show()

def texture_options():
    from . import texture_tools
    return texture_tools.configure_selected(
        bits=32 if cmds.optionMenuGrp(CONTROLS['texbits'],q=True,select=True)==1 else 16,
        maximum=int(cmds.optionMenuGrp(CONTROLS['texmax'],q=True,value=True)),
        resize=cmds.checkBox(CONTROLS['resize'],q=True,value=True),
        decal=cmds.checkBox(CONTROLS['decal'],q=True,value=True),
        auto_crop=cmds.checkBox(CONTROLS['autocrop'],q=True,value=True))


def export_glb():
    from .vendor import glb_export
    result = cmds.fileDialog2(fileMode=0, fileFilter="glTF Binary (*.glb)", caption="Export selected F3D meshes")
    if not result:
        return {"cancelled": True}
    attributes.prepare()
    meshes = scene.selected_meshes()
    if any(cmds.ls(cmds.listHistory(mesh), type="skinCluster") for mesh in meshes):
        from .vendor import animated_export
        return animated_export.export_glb(result[0])
    return glb_export.export_glb(result[0], selected=True, f3d=True)


def install_menu():
    if cmds.about(batch=True):
        return
    if cmds.menu(MENU, exists=True):
        cmds.deleteUI(MENU)
    parent = mel.eval("$fast64MainWindow = $gMainWindow")
    cmds.menu(MENU, label="Fast64", parent=parent, tearOff=True)
    cmds.menuItem(label="Fast64 Tools…", command=lambda *_: show())
    cmds.menuItem(label="Convert Maya Materials to F3D", command=lambda *_: safely(scene.convert_materials))
    cmds.menuItem(label="Paint Vertex Colors", command=lambda *_: safely(paint.start))


def remove_ui():
    for name, query in ((WINDOW, cmds.window), (MENU, cmds.menu)):
        if query(name, exists=True):
            cmds.deleteUI(name)
