"""Native skin and sampled joint-animation export built on the F3D GLB exporter."""
import json
import math
import os
from pathlib import Path
import struct
import tempfile
import maya.cmds as cmds
import maya.api.OpenMaya as om
import maya.api.OpenMayaAnim as oma
from . import glb_export


def dag_path(name):
    selection = om.MSelectionList()
    selection.add(name)
    return selection.getDagPath(0)


def matrix_values(matrix):
    values = list(matrix)
    values[12:15] = [v * 0.01 for v in values[12:15]]
    return values


def trs(matrix):
    transform = om.MTransformationMatrix(matrix)
    translation = transform.translation(om.MSpace.kTransform)
    rotation = transform.rotation(asQuaternion=True)
    scale = transform.scale(om.MSpace.kTransform)
    if any(abs(v) > 1e-6 for v in transform.shear(om.MSpace.kTransform)):
        raise ValueError('Animated shear cannot be exported as glTF joint TRS')
    return [translation.x * .01, translation.y * .01, translation.z * .01], list(rotation), list(scale)


def read_static(path):
    raw = Path(path).read_bytes()
    length = struct.unpack_from('<I', raw, 12)[0]
    doc = json.loads(raw[20:20 + length])
    binary = raw[28 + length:]
    return doc, binary


def accessor(doc, binary, index):
    item = doc['accessors'][index]
    view = doc['bufferViews'][item['bufferView']]
    width = {'VEC3':3, 'VEC4':4}[item['type']]
    offset = view.get('byteOffset', 0) + item.get('byteOffset', 0)
    return [struct.unpack_from('<' + 'f' * width, binary, offset + i * width * 4) for i in range(item['count'])]


def validate_clips(clips):
    if not isinstance(clips, list) or not clips:
        raise ValueError('Specify at least one named animation range')
    names = set()
    result = []
    for clip in clips:
        name = str(clip['name']).strip()
        start, end = float(clip['start']), float(clip['end'])
        if not name or name in names:
            raise ValueError('Animation names must be nonempty and unique')
        if not math.isfinite(start) or not math.isfinite(end) or end <= start:
            raise ValueError('Each animation needs a finite end frame after its start frame')
        names.add(name)
        result.append({'name':name, 'start':start, 'end':end})
    return result


def export_glb(path, start=None, end=None, sample_step=1.0, clips=None):
    """Export one selected linear-skinned mesh, retaining up to four weights."""
    path = Path(path).absolute()
    if path.exists():
        raise ValueError('Output already exists: ' + str(path))
    if cmds.upAxis(query=True, axis=True) != 'y':
        raise ValueError('Skinned export currently requires a Y-up scene')
    selected = cmds.ls(selection=True, long=True, objectsOnly=True)
    meshes = list(dict.fromkeys(cmds.ls(selected, dag=True, type='mesh', noIntermediate=True, long=True) or []))
    if len(meshes) != 1:
        raise ValueError('Select one skinned polygon mesh')
    mesh = meshes[0]
    skins = cmds.ls(cmds.listHistory(mesh), type='skinCluster') or []
    if len(skins) != 1:
        raise ValueError('The mesh must have exactly one skinCluster')
    skin = skins[0]
    if cmds.getAttr(skin + '.skinningMethod') != 0:
        raise ValueError('Use linear skinning; dual-quaternion skinning is not supported by glTF')
    if cmds.listConnections(skin + '.envelope', source=True, destination=False):
        raise ValueError('Animated skin envelopes are unsupported')
    sl = om.MSelectionList(); sl.add(skin)
    fn_skin = oma.MFnSkinCluster(sl.getDependNode(0))
    dag = dag_path(mesh)
    influences = list(fn_skin.influenceObjects())
    names = [p.fullPathName() for p in influences]
    component = om.MFnSingleIndexedComponent()
    vertices = component.create(om.MFn.kMeshVertComponent)
    component.setCompleteData(om.MFnMesh(dag).numVertices)
    weights, count = fn_skin.getWeights(dag, vertices)
    vertex_weights = []
    for vertex in range(component.elementCount):
        row = sorted([(names[j], weights[vertex * count + j]) for j in range(count) if weights[vertex * count + j] > 1e-7], key=lambda a: -a[1])
        if len(row) > 4 or not row:
            raise ValueError('Each vertex must have one to four nonzero skin influences')
        total = sum(w for _, w in row)
        vertex_weights.append([(n, w / total) for n, w in row])
    node_names = set(names)
    for name in names:
        parent = name.rsplit('|', 1)[0]
        while parent:
            node_names.add(parent)
            parent = parent.rsplit('|', 1)[0]
    node_names = sorted(node_names, key=lambda n: (n.count('|'), n))
    # Depth-first node order keeps the glTF joint indices consistent with bone traversal.
    ordered = []
    def visit(name):
        ordered.append(name)
        for child in node_names:
            if child.rsplit('|', 1)[0] == name:
                visit(child)
    for name in node_names:
        if name.rsplit('|', 1)[0] not in node_names:
            visit(name)
    node_names = ordered
    index_map = {name:i for i,name in enumerate(node_names)}
    parent = cmds.listRelatives(mesh, parent=True, fullPath=True)[0]
    if clips is None and start is None and end is None and cmds.attributeQuery('fast64AnimationClips', node=parent, exists=True):
        stored = cmds.getAttr(parent + '.fast64AnimationClips')
        clips = json.loads(stored) if stored else None
    if clips is None:
        start = cmds.playbackOptions(query=True, min=True) if start is None else start
        end = cmds.playbackOptions(query=True, max=True) if end is None else end
        clips = [{'name':'animation_00', 'start':start, 'end':end}]
    clips = validate_clips(clips)
    if not math.isfinite(sample_step) or sample_step <= 0:
        raise ValueError('Sample step must be positive and finite')
    clip_frames = [[clip['start'] + i * sample_step for i in range(int(math.ceil((clip['end']-clip['start'])/sample_step)))] + [clip['end']] for clip in clips]
    frames = sorted(set(frame for clip in clip_frames for frame in clip))
    start = frames[0]
    fps = 1.0 / om.MTime(1, om.MTime.uiUnit()).asUnits(om.MTime.kSeconds)
    original_time = cmds.currentTime(query=True)
    envelope = cmds.getAttr(skin + '.envelope')
    parent = cmds.listRelatives(mesh, parent=True, fullPath=True)[0]
    try:
        cmds.currentTime(start, edit=True)
        rest_world = {name:dag_path(name).inclusiveMatrix() for name in node_names}
        for influence, name in zip(influences, names):
            logical_index = fn_skin.indexForInfluenceObject(influence)
            inverse = om.MMatrix(cmds.getAttr(skin + '.bindPreMatrix[{}]'.format(logical_index)))
            rest_world[name] = inverse.inverse()
        cmds.setAttr(skin + '.envelope', 0)
        rest_fn = om.MFnMesh(dag_path(mesh))
        points = rest_fn.getPoints(om.MSpace.kWorld)
        lookup = {}
        for vertex, point in enumerate(points):
            key = tuple(round(v * .01, 6) for v in (point.x, point.y, point.z))
            row = vertex_weights[vertex]
            if key in lookup and lookup[key] != row:
                raise ValueError('Coincident vertices with different weights require explicit vertex mapping')
            lookup[key] = row
        with tempfile.TemporaryDirectory() as temp:
            temporary = Path(temp) / path.name
            stats = glb_export.export_glb(str(temporary), selected=True, f3d=True)
            doc, binary = read_static(temporary)
            builder = glb_export.Builder()
            builder.doc = doc
            builder.binary = bytearray(binary[:doc['buffers'][0]['byteLength']])
            for primitive in doc['meshes'][0]['primitives']:
                rows = []
                for position in accessor(doc, binary, primitive['attributes']['POSITION']):
                    key = tuple(round(v, 6) for v in position)
                    if key not in lookup:
                        key = min(lookup, key=lambda k:sum((k[i]-position[i])**2 for i in range(3)))
                        if max(abs(key[i]-position[i]) for i in range(3)) > 2e-6:
                            raise ValueError('Cannot map exported vertices to bind geometry')
                    rows.append(lookup[key])
                joints = [tuple([index_map[n] for n,w in row] + [0]*(4-len(row))) for row in rows]
                values = [tuple([w for n,w in row] + [0.]*(4-len(row))) for row in rows]
                primitive['attributes']['JOINTS_0'] = builder.accessor(joints, 'VEC4', component=5123)
                primitive['attributes']['WEIGHTS_0'] = builder.accessor(values, 'VEC4')
            mesh_node = doc['nodes'][0]
            doc['nodes'] = []
            roots = []
            for name in node_names:
                parent_name = name.rsplit('|', 1)[0]
                local = rest_world[name] * rest_world[parent_name].inverse() if parent_name in index_map else rest_world[name]
                translation, rotation, scale = trs(local)
                node = {'name':name.split('|')[-1], 'translation':translation, 'rotation':rotation, 'scale':scale}
                children = [index_map[n] for n in node_names if n.rsplit('|',1)[0] == name]
                if children: node['children'] = children
                doc['nodes'].append(node)
                if parent_name not in index_map: roots.append(index_map[name])
            mesh_node['skin'] = 0
            doc['nodes'].append(mesh_node)
            roots.append(len(node_names))
            doc['scenes'][0]['nodes'] = roots
            inverse_bind = [matrix_values(rest_world[n].inverse()) for n in node_names]
            doc['skins'] = [{'joints':list(range(len(node_names))), 'inverseBindMatrices':builder.accessor(inverse_bind,'MAT4',target=None)}]
            cmds.setAttr(skin + '.envelope', envelope)
            samples = {name:{'translation':[], 'rotation':[], 'scale':[]} for name in node_names}
            for frame in frames:
                cmds.currentTime(frame, edit=True)
                world = {name:dag_path(name).inclusiveMatrix() for name in node_names}
                for name in node_names:
                    parent_name = name.rsplit('|',1)[0]
                    local = world[name] * world[parent_name].inverse() if parent_name in world else world[name]
                    translation, rotation, scale = trs(local)
                    previous = samples[name]['rotation']
                    if previous and sum(a*b for a,b in zip(previous[-1],rotation)) < 0:
                        rotation = [-v for v in rotation]
                    for key,value in [('translation',translation),('rotation',rotation),('scale',scale)]: samples[name][key].append(value)
            frame_indices = {frame:i for i,frame in enumerate(frames)}
            doc['animations'] = []
            for clip, clip_samples in zip(clips, clip_frames):
                times = builder.accessor([((frame-clip['start'])/fps,) for frame in clip_samples], 'SCALAR', target=None, bounds=True)
                animation = {'name':clip['name'], 'samplers':[], 'channels':[]}
                for name in node_names:
                    for channel, values in samples[name].items():
                        clip_values = [values[frame_indices[frame]] for frame in clip_samples]
                        sampler = {'input':times, 'output':builder.accessor(clip_values,'VEC4' if channel=='rotation' else 'VEC3',target=None), 'interpolation':'LINEAR'}
                        animation['channels'].append({'sampler':len(animation['samplers']), 'target':{'node':index_map[name], 'path':channel}})
                        animation['samplers'].append(sampler)
                doc['animations'].append(animation)
            path.parent.mkdir(parents=True, exist_ok=True)
            for image in doc.get('images', []):
                if 'uri' in image:
                    from urllib.parse import unquote
                    filename = unquote(image['uri'])
                    data = (Path(temp)/filename).read_bytes()
                    output = path.parent / filename
                    if output.exists() and output.read_bytes() != data:
                        raise ValueError('Existing texture differs: ' + str(output))
                    output.write_bytes(data)
            path.write_bytes(builder.finish())
            stats.update({'path':str(path), 'bones':len(node_names), 'animationFrames':len(frames), 'clips':[clip['name'] for clip in clips], 'multiWeightVertices':sum(len(row)>1 for row in vertex_weights)})
            return stats
    finally:
        cmds.setAttr(skin + '.envelope', envelope)
        cmds.currentTime(original_time, edit=True)
        cmds.select(selected, replace=True)
