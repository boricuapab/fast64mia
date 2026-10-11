"""Fast64 vertex color/alpha color sets and UVMap preparation for Maya meshes."""
import maya.cmds as cmds
import maya.api.OpenMaya as om
from . import scene


def prepare():
    """Preserve painted RGB/RGBA, add Col/Alpha, and rename map1 to UVMap.

    Col stores RGBA painting; Alpha stores the same alpha in its RGB channels,
    as expected by Fast64. Running on active Alpha preserves independent alpha
    edits; running on Col refreshes the companion from Col's alpha channel.
    """
    meshes = scene.selected_meshes()
    selection = cmds.ls(selection=True, long=True) or []
    for mesh in meshes:
        uvsets = cmds.polyUVSet(mesh, query=True, allUVSets=True) or []
        if not uvsets:
            raise ValueError(mesh + ": no UV set exists; create UVs first")
        if "map1" in uvsets and "UVMap" in uvsets:
            raise ValueError(mesh + ": both map1 and UVMap exist; choose/resolve the UV sets first")
    results = []
    cmds.undoInfo(openChunk=True, chunkName="Fast64 prepare Col Alpha UVMap")
    try:
        for mesh in meshes:
            sets = cmds.polyColorSet(mesh, query=True, allColorSets=True) or []
            current = (cmds.polyColorSet(mesh, query=True, currentColorSet=True) or [None])[0]
            if "Col" not in sets:
                if current and current != "Alpha":
                    cmds.polyColorSet(mesh, copy=True, colorSet=current, newColorSet="Col")
                else:
                    cmds.polyColorSet(mesh, create=True, colorSet="Col", representation="RGBA", clamped=True)
                    cmds.polyColorSet(mesh, currentColorSet=True, colorSet="Col")
                    cmds.polyColorPerVertex(mesh, rgb=(1, 1, 1), alpha=1)
            cmds.polyColorSet(mesh, currentColorSet=True, colorSet="Col")
            _, fn = scene.mesh_fn(mesh)
            colors = fn.getFaceVertexColors("Col", om.MColor((1, 1, 1, 1)))
            sets = cmds.polyColorSet(mesh, query=True, allColorSets=True) or []
            use_existing_alpha = "Alpha" in sets and current == "Alpha"
            if "Alpha" not in sets:
                cmds.polyColorSet(mesh, create=True, colorSet="Alpha", representation="RGBA", clamped=True)
            if not use_existing_alpha:
                cmds.polyColorSet(mesh, currentColorSet=True, colorSet="Alpha")
                # Use Maya commands rather than API writes so the entire preparation is undoable.
                vertex_counts, vertex_ids = fn.getVertices()
                offset = 0
                for face, count in enumerate(vertex_counts):
                    for local in range(count):
                        alpha = max(0.0, min(1.0, colors[offset + local].a))
                        component = "{}.vtxFace[{}][{}]".format(mesh, vertex_ids[offset + local], face)
                        cmds.polyColorPerVertex(component, rgb=(alpha, alpha, alpha), alpha=1)
                    offset += count
            cmds.polyColorSet(mesh, currentColorSet=True, colorSet="Col")
            uvsets = cmds.polyUVSet(mesh, query=True, allUVSets=True)
            if "UVMap" not in uvsets:
                source = "map1" if "map1" in uvsets else cmds.polyUVSet(mesh, query=True, currentUVSet=True)[0]
                cmds.polyUVSet(mesh, rename=True, uvSet=source, newUVSet="UVMap")
            cmds.polyUVSet(mesh, currentUVSet=True, uvSet="UVMap")
            cmds.setAttr(mesh + ".displayColors", True)
            results.append({"mesh": mesh, "color_sets": ["Col", "Alpha"], "uv_set": "UVMap"})
        cmds.select(selection, replace=True)
    except Exception:
        cmds.undoInfo(closeChunk=True)
        cmds.undo()
        raise
    else:
        cmds.undoInfo(closeChunk=True)
    return results


def colors_for_mesh(fn):
    sets = fn.getColorSetNames()
    source = "Col" if "Col" in sets else (fn.currentColorSetName() if sets else None)
    if not source:
        return None
    colors = fn.getFaceVertexColors(source, om.MColor((1, 1, 1, 1)))
    alpha = fn.getFaceVertexColors("Alpha", om.MColor((1, 1, 1, 1))) if "Col" in sets and "Alpha" in sets else None
    if alpha is not None:
        return [tuple((c.r, c.g, c.b, alpha[i].r)) for i, c in enumerate(colors)]
    return [(c.r, c.g, c.b, c.a) for c in colors]
