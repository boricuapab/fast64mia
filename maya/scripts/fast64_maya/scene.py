"""Maya API 2.0 scene extraction and native F3D material conversion."""
import json
import os
import tempfile
from collections import OrderedDict

import maya.cmds as cmds
import maya.api.OpenMaya as om
from PySide2.QtGui import QImage

from . import core

MODES = ("VertexColor", "TextureVertexColor", "Primitive", "Shaded", "TextureShaded")
LAYERS = ("Opaque", "Transparent", "Cutout")
SUPPORTED_SHADERS = ("lambert", "blinn", "phong", "phongE", "surfaceShader", "aiStandardSurface", "standardSurface")


def selected_meshes():
    selected = cmds.ls(selection=True, long=True, objectsOnly=True) or []
    meshes = cmds.ls(selected, dag=True, type="mesh", long=True, noIntermediate=True) if selected else []
    meshes = list(OrderedDict.fromkeys(meshes or []))
    if not meshes:
        raise ValueError("Select one or more polygon meshes or their parent transforms")
    return meshes


def mesh_fn(path):
    selection = om.MSelectionList()
    selection.add(path)
    dag = selection.getDagPath(0)
    return dag, om.MFnMesh(dag)


def attr(node, name, default):
    if not cmds.attributeQuery(name, node=node, exists=True):
        return default
    value = cmds.getAttr(node + "." + name)
    if isinstance(value, list) and len(value) == 1 and isinstance(value[0], tuple):
        value = value[0]
    return value


def add(node, name, value, kind="string", enum=None):
    if not cmds.attributeQuery(name, node=node, exists=True):
        if kind == "string":
            cmds.addAttr(node, longName=name, dataType="string")
        else:
            options = {"longName": name, "attributeType": kind}
            if enum:
                options["enumName"] = ":".join(enum)
            cmds.addAttr(node, **options)
    if kind == "string":
        cmds.setAttr(node + "." + name, value, type="string")
    else:
        cmds.setAttr(node + "." + name, value)


def tag_material(shader, mode="Shaded", layer="Opaque", texture="", source=""):
    add(shader, "fast64Material", True, "bool")
    add(shader, "fast64Mode", MODES.index(mode), "enum", MODES)
    add(shader, "fast64Layer", LAYERS.index(layer), "enum", LAYERS)
    add(shader, "fast64CullBack", True, "bool")
    add(shader, "fast64Texture", texture)
    add(shader, "fast64SourceMaterial", source)
    add(shader, "fast64CollisionSurface", "SURFACE_DEFAULT")
    add(shader, "fast64RepeatU", 1.0, "double")
    add(shader, "fast64RepeatV", 1.0, "double")
    add(shader, "fast64OffsetU", 0.0, "double")
    add(shader, "fast64OffsetV", 0.0, "double")
    add(shader, "fast64WrapU", True, "bool")
    add(shader, "fast64WrapV", True, "bool")


def new_material(name="F3D_Material"):
    shader = cmds.shadingNode("lambert", asShader=True, name=name, skipSelect=True)
    group = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=shader + "SG")
    cmds.connectAttr(shader + ".outColor", group + ".surfaceShader", force=True)
    cmds.setAttr(shader + ".color", 1, 1, 1, type="double3")
    cmds.setAttr(shader + ".diffuse", 1)
    tag_material(shader)
    return shader, group


def create_material():
    original_selection = cmds.ls(selection=True, long=True) or []
    meshes = selected_meshes()
    cmds.undoInfo(openChunk=True, chunkName="Create F3D material")
    try:
        shader, group = new_material()
        cmds.sets(meshes, edit=True, forceElement=group)
        return shader
    finally:
        cmds.select(original_selection, replace=True)
        cmds.undoInfo(closeChunk=True)


def shader_for_group(group):
    shaders = cmds.listConnections(group + ".surfaceShader", source=True, destination=False) or []
    if len(shaders) != 1:
        raise ValueError("{} has no single surface shader".format(group))
    return shaders[0]


def source_description(shader):
    kind = cmds.nodeType(shader)
    if kind not in SUPPORTED_SHADERS:
        raise ValueError("{}: unsupported shader {}".format(shader, kind))
    color_attr = "baseColor" if kind in ("aiStandardSurface", "standardSurface") else (
        "outColor" if kind == "surfaceShader" else "color")
    plug = shader + "." + color_attr
    incoming = cmds.listConnections(plug, source=True, destination=False, plugs=True) or []
    # Compound child connections and procedural networks require baking first.
    child_incoming = []
    for suffix in ("R", "G", "B"):
        if cmds.objExists(plug + suffix):
            child_incoming.extend(cmds.listConnections(plug + suffix, source=True, destination=False, plugs=True) or [])
    if child_incoming and not incoming:
        raise ValueError("{}: channel texture networks must be baked to one file texture".format(shader))
    texture_node = None
    if incoming:
        nodes = list(OrderedDict.fromkeys(p.split(".")[0] for p in incoming))
        if len(nodes) != 1 or cmds.nodeType(nodes[0]) != "file" or any(not p.endswith(".outColor") for p in incoming):
            raise ValueError("{}: bake the diffuse network to a file.outColor texture first".format(shader))
        texture_node = nodes[0]
    color = tuple(attr(shader, color_attr, (1.0, 1.0, 1.0))) if not texture_node else (1.0, 1.0, 1.0)
    diffuse = attr(shader, "base" if kind in ("aiStandardSurface", "standardSurface") else "diffuse", 1.0)
    if not isinstance(diffuse, (int, float)):
        raise ValueError("{}: diffuse weight must be a constant".format(shader))
    color = tuple(c * diffuse for c in color)
    alpha = 1.0
    opacity_attr = "opacity" if kind in ("aiStandardSurface", "standardSurface") else "transparency"
    opacity = attr(shader, opacity_attr, (1, 1, 1) if opacity_attr == "opacity" else (0, 0, 0))
    incoming_opacity = cmds.listConnections(shader + "." + opacity_attr, source=True, destination=False, plugs=True) or []
    if incoming_opacity:
        expected = texture_node + ".outTransparency" if texture_node else None
        if opacity_attr != "transparency" or incoming_opacity != [expected]:
            raise ValueError("{}: separate opacity networks require baking into diffuse image alpha".format(shader))
    else:
        if max(opacity) - min(opacity) > 0.001:
            raise ValueError("{}: colored transparency is unsupported".format(shader))
        alpha = opacity[0] if opacity_attr == "opacity" else 1 - opacity[0]
    description = {"color": color, "alpha": alpha, "texture_node": texture_node, "texture": "",
                   "repeat": (1, 1), "offset": (0, 0), "wrap": (True, True), "layer": "Transparent" if alpha < 0.999 else "Opaque"}
    if texture_node:
        if attr(texture_node, "uvTilingMode", 0) or attr(texture_node, "useFrameExtension", False):
            raise ValueError("{}: UDIM/animated textures must be baked to a single image".format(shader))
        for name, expected in (("rotateUV", 0), ("rotateFrame", 0), ("translateFrame", (0, 0)),
                               ("coverage", (1, 1)), ("mirrorU", False), ("mirrorV", False), ("stagger", False),
                               ("noiseUV", (0, 0)), ("colorGain", (1, 1, 1)), ("colorOffset", (0, 0, 0)),
                               ("alphaGain", 1), ("alphaOffset", 0), ("alphaIsLuminance", False)):
            if attr(texture_node, name, expected) != expected:
                raise ValueError("{}: non-default file.{} needs texture baking".format(shader, name))
        uv_sources = cmds.listConnections(texture_node + ".uvCoord", source=True, destination=False) or []
        if any(cmds.nodeType(n) != "place2dTexture" for n in uv_sources):
            raise ValueError("{}: nonstandard UV routing needs baking".format(shader))
        description["texture"] = attr(texture_node, "fileTextureName", "")
        description["repeat"] = attr(texture_node, "repeatUV", (1, 1))
        description["offset"] = attr(texture_node, "offset", (0, 0))
        description["wrap"] = (attr(texture_node, "wrapU", True), attr(texture_node, "wrapV", True))
    return description


def convert_materials():
    """Convert assignments on selected meshes; leave original shaders available."""
    original_selection = cmds.ls(selection=True, long=True) or []
    plans, descriptions, warnings = [], {}, []
    for path in selected_meshes():
        dag, fn = mesh_fn(path)
        groups, indices = fn.getConnectedShaders(dag.instanceNumber())
        for index, group_obj in enumerate(groups):
            old_group = om.MFnDependencyNode(group_obj).name()
            shader = shader_for_group(old_group)
            if attr(shader, "fast64Material", False):
                continue
            if shader not in descriptions:
                descriptions[shader] = source_description(shader)
            faces = ["{}.f[{}]".format(path, i) for i, material_index in enumerate(indices) if material_index == index]
            if faces:
                plans.append((shader, faces, path, len(faces) == fn.numPolygons))
    converted = {}
    cmds.undoInfo(openChunk=True, chunkName="Convert Maya materials to F3D")
    try:
        for original, description in descriptions.items():
            shader, group = new_material(original + "_F3D")
            mode = "TextureShaded" if description["texture"] else "Shaded"
            tag_material(shader, mode, description["layer"], description["texture"], original)
            cmds.setAttr(shader + ".color", *description["color"], type="double3")
            transparency = 1 - description["alpha"]
            cmds.setAttr(shader + ".transparency", transparency, transparency, transparency, type="double3")
            for name, value in zip(("fast64RepeatU", "fast64RepeatV", "fast64OffsetU", "fast64OffsetV",
                                    "fast64WrapU", "fast64WrapV"), description["repeat"] + description["offset"] + description["wrap"]):
                cmds.setAttr(shader + "." + name, value)
            if description["texture_node"]:
                cmds.connectAttr(description["texture_node"] + ".outColor", shader + ".color", force=True)
                # F3D tint is stored separately when Maya's preview color has a texture connection.
                add(shader, "fast64Tint", json.dumps(description["color"]))
                add(shader, "fast64Opacity", description["alpha"], "double")
                preview = cmds.shadingNode("blendColors", asUtility=True, name=shader + "_Opacity", skipSelect=True)
                cmds.setAttr(preview + ".color1", 1, 1, 1, type="double3")
                cmds.setAttr(preview + ".blender", 1 - description["alpha"])
                cmds.connectAttr(description["texture_node"] + ".outTransparency", preview + ".color2")
                cmds.connectAttr(preview + ".output", shader + ".transparency", force=True)
            converted[original] = (shader, group)
            if cmds.nodeType(original) in ("blinn", "phong", "phongE", "aiStandardSurface", "standardSurface"):
                warnings.append("{}: specular/reflection/emission converted to diffuse N64 shading".format(original))
        for original, faces, path, whole_mesh in plans:
            cmds.sets(path if whole_mesh else faces, edit=True, forceElement=converted[original][1])
        verify_material_assignments()
    except Exception:
        cmds.undoInfo(closeChunk=True)
        cmds.undo()
        raise
    else:
        cmds.select(original_selection, replace=True)
        cmds.undoInfo(closeChunk=True)
    for warning in warnings:
        cmds.warning(warning)
    return {"converted": [pair[0] for pair in converted.values()], "warnings": warnings}


def verify_material_assignments():
    """Report the shaders that actually own polygon faces; ignore empty legacy connections."""
    result = []
    for path in selected_meshes():
        dag, fn = mesh_fn(path)
        groups, indices = fn.getConnectedShaders(dag.instanceNumber())
        active = []
        for index, group in enumerate(groups):
            count = sum(i == index for i in indices)
            if count:
                name = om.MFnDependencyNode(group).name()
                shader = shader_for_group(name)
                if not attr(shader, "fast64Material", False):
                    raise ValueError("{}: {} still owns {} faces; convert the selected mesh".format(path, shader, count))
                active.append({"shader": shader, "group": name, "faces": count})
        if sum(m["faces"] for m in active) != fn.numPolygons:
            raise ValueError("{} has unassigned faces".format(path))
        result.append({"mesh": path, "assignments": active})
    return result


def load_texture(filename):
    filename = os.path.expandvars(os.path.expanduser(filename))
    if not os.path.isabs(filename):
        filename = cmds.workspace(expandName=filename)
    image = QImage(filename)
    if image.isNull():
        raise ValueError("Cannot read texture: " + filename)
    width, height = image.width(), image.height()
    # Validate before reading a potentially large image into Python memory.
    if width < 4 or height < 1 or width > 256 or height > 256 or width & (width-1) or height & (height-1):
        raise ValueError("{}: use a power-of-two RGBA16 texture, width 4..256, height 1..256".format(filename))
    if width * height * 2 > 4096:
        raise ValueError("{}: texture exceeds 4 KiB TMEM (e.g. use 32x32 or 64x32)".format(filename))
    pixels = []
    for y in range(height):
        for x in range(width):
            c = image.pixelColor(x, y)
            pixels.append((c.redF(), c.greenF(), c.blueF(), c.alphaF()))
    return {"width": width, "height": height, "pixels": pixels, "path": filename}


def material_description(shader, textures, require_texture=True):
    if require_texture and (attr(shader,'fast64AlphaOverVertex',False) or attr(shader,'fast64TextureBits',0)):
        raise ValueError('Alpha decals and RGBA32 materials currently require F3D GLB export')
    if not attr(shader, "fast64Material", False):
        raise ValueError("{} is not an F3D material; use Convert Maya Materials first".format(shader))
    mode = MODES[attr(shader, "fast64Mode", 3)]
    color = tuple(attr(shader, "color", (1, 1, 1)))
    tint = attr(shader, "fast64Tint", "")
    if tint:
        color = tuple(json.loads(tint))
    alpha = attr(shader, "fast64Opacity", 1 - attr(shader, "transparency", (0, 0, 0))[0])
    material = {"mode": mode, "layer": LAYERS[attr(shader, "fast64Layer", 0)],
                "cull_back": attr(shader, "fast64CullBack", True), "color": color + (alpha,),
                "repeat": (attr(shader, "fast64RepeatU", 1), attr(shader, "fast64RepeatV", 1)),
                "offset": (attr(shader, "fast64OffsetU", 0), attr(shader, "fast64OffsetV", 0)),
                "wrap_s": attr(shader, "fast64WrapU", True), "wrap_t": attr(shader, "fast64WrapV", True)}
    if mode in ("VertexColor", "TextureVertexColor"):
        # SHADE reads painted RGBA; the old Lambert diffuse color is not an additional combiner source.
        material["color"] = (1, 1, 1, alpha)
    if require_texture and mode in ("TextureShaded", "TextureVertexColor"):
        filename = attr(shader, "fast64Texture", "")
        if filename not in textures:
            textures[filename] = load_texture(filename)
        material["texture"] = textures[filename]
        if any(p[3] < 0.5 for p in material["texture"]["pixels"]) and material["layer"] == "Opaque":
            material["layer"] = "Cutout"
    return material


def gather(scale=1.0, collision_only=False):
    if scale <= 0:
        raise ValueError("Scale must be positive")
    groups, materials, textures = OrderedDict(), {}, {}
    z_up = cmds.upAxis(query=True, axis=True) == "z"

    def coordinates(v):
        values = (v.x, v.z, -v.y) if z_up else (v.x, v.y, v.z)
        return tuple(c * scale for c in values)

    for path in selected_meshes():
        dag, fn = mesh_fn(path)
        shading_groups, assignments = fn.getConnectedShaders(dag.instanceNumber())
        mirrored = dag.inclusiveMatrix().det4x4() < 0
        from .attributes import colors_for_mesh
        face_colors = colors_for_mesh(fn)
        has_colors = face_colors is not None
        iterator = om.MItMeshPolygon(dag)
        while not iterator.isDone():
            face = iterator.index()
            material_index = assignments[face]
            if collision_only:
                shader = None
                surface = "SURFACE_DEFAULT"
                if material_index >= 0:
                    shader = shader_for_group(om.MFnDependencyNode(shading_groups[material_index]).name())
                    surface = attr(shader, "fast64CollisionSurface", surface)
                # Static default and slippery surfaces do not need COL_TRI_SPECIAL parameters.
                allowed = ("SURFACE_DEFAULT", "SURFACE_BURNING", "SURFACE_SLIPPERY", "SURFACE_VERY_SLIPPERY",
                           "SURFACE_NOT_SLIPPERY", "SURFACE_DEATH_PLANE", "SURFACE_HANGABLE")
                if surface not in allowed:
                    raise ValueError("{}: unsupported collision surface (special parameters are not implemented)".format(surface))
                key = surface
                groups.setdefault(key, {"surface": surface, "triangles": []})
                material = None
            else:
                if material_index < 0:
                    raise ValueError("{} face {} has no material".format(path, face))
                shader = shader_for_group(om.MFnDependencyNode(shading_groups[material_index]).name())
                if shader not in materials:
                    materials[shader] = material_description(shader, textures)
                material = materials[shader]
                key = shader
                groups.setdefault(key, {"material": material, "triangles": []})
            points, vertex_ids = iterator.getTriangles(om.MSpace.kWorld)
            face_vertex_ids = list(iterator.getVertices())
            for start in range(0, len(vertex_ids), 3):
                corners = []
                for i in range(start, start + 3):
                    vertex_id = vertex_ids[i]
                    corner = {"position": coordinates(points[i])}
                    if not collision_only:
                        color = (1, 1, 1, 1)
                        if has_colors:
                            c = face_colors[fn.getFaceVertexIndex(face, vertex_id, False)]
                            if c[0] >= 0 and c[1] >= 0 and c[2] >= 0:
                                color = c
                        corner["color"] = tuple(color[j] * material["color"][j] for j in range(4))
                        normal = fn.getFaceVertexNormal(face, vertex_id, om.MSpace.kWorld)
                        normal.normalize()
                        corner["normal"] = (normal.x, normal.z, -normal.y) if z_up else (normal.x, normal.y, normal.z)
                        if material["mode"] in ("TextureShaded", "TextureVertexColor"):
                            local = face_vertex_ids.index(vertex_id)
                            try:
                                u, v = iterator.getUV(local)
                            except RuntimeError:
                                raise ValueError("{} face {} is missing UVs".format(path, face))
                            corner["uv"] = (u * material["repeat"][0] + material["offset"][0],
                                            v * material["repeat"][1] + material["offset"][1])
                    corners.append(corner)
                if mirrored:
                    corners.reverse()
                groups[key]["triangles"].append(corners)
            iterator.next()
    return list(groups.values())


def export(directory, name="model", scale=1.0, microcode="F3DEX2", collision_only=False):
    groups = gather(scale, collision_only)
    writer = core.collision if collision_only else core.display_list
    source, header, stats = writer(name, groups) if collision_only else writer(name, groups, microcode)
    target = os.path.join(os.path.abspath(directory), core.identifier(name))
    os.makedirs(target, exist_ok=True)
    basename = "collision.inc.c" if collision_only else "model.inc.c"
    headername = "collision.h" if collision_only else "header.h"
    paths = []
    for filename, content in ((basename, source), (headername, header)):
        output = os.path.join(target, filename)
        if os.path.exists(output):
            raise ValueError("Output already exists: {}. Choose a new name or directory.".format(output))
        paths.append((output, content))
    # Validate every mesh/material before creating files, and publish each file atomically.
    for output, content in paths:
        fd, temporary = tempfile.mkstemp(dir=target, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", newline="\n") as stream:
                stream.write(content)
            os.replace(temporary, output)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    stats["directory"] = target
    return stats
