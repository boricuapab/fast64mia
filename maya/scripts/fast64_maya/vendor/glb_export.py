"""Native Maya static GLB export with Fast64 custom properties for F3D.

No Blender dependency. glTF units are metres; Maya API positions are centimetres.
Exports Fast64 material extras alongside standard glTF material properties.
"""
import hashlib
import json
import math
import os
import struct
import tempfile
from collections import OrderedDict
from urllib.parse import quote

import maya.cmds as cmds
import maya.api.OpenMaya as om
from PySide2.QtGui import QImage
from PySide2.QtCore import QByteArray, QBuffer, QIODevice


def _attr(node, name, default):
    if not cmds.attributeQuery(name, node=node, exists=True):
        return default
    result = cmds.getAttr(node+'.'+name)
    return result[0] if isinstance(result, list) and result and isinstance(result[0], tuple) else result


def _clamp(value):
    if not math.isfinite(value):
        raise ValueError('Non-finite vertex/material value')
    return max(0.0, min(1.0, value))


class Builder:
    def __init__(self):
        self.binary = bytearray()
        self.doc = {'asset': {'version': '2.0', 'generator': 'Fast64 Maya / Native GLB 1.2'},
                    'scene': 0, 'scenes': [{'nodes': []}], 'nodes': [], 'meshes': [],
                    'materials': [], 'bufferViews': [], 'accessors': []}
        self.extensions = set()

    def view(self, data, target=None):
        self.binary.extend(b'\0' * (-len(self.binary) % 4))
        view = {'buffer': 0, 'byteOffset': len(self.binary), 'byteLength': len(data)}
        if target:
            view['target'] = target
        self.binary.extend(data)
        self.doc['bufferViews'].append(view)
        return len(self.doc['bufferViews'])-1

    def accessor(self, values, kind, component=5126, target=34962, bounds=False):
        widths = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4, 'MAT4': 16}
        width = widths[kind]
        code = {5126: 'f', 5125: 'I', 5123: 'H'}[component]
        data = b''.join(struct.pack('<'+code*width, *v) for v in values)
        access = {'bufferView': self.view(data, target), 'componentType': component,
                  'count': len(values), 'type': kind}
        if bounds:
            access['min'] = [min(v[i] for v in values) for i in range(width)]
            access['max'] = [max(v[i] for v in values) for i in range(width)]
        self.doc['accessors'].append(access)
        return len(self.doc['accessors'])-1

    def finish(self):
        size = len(self.binary)
        self.doc['buffers'] = [{'byteLength': size}]
        if self.extensions:
            self.doc['extensionsUsed'] = sorted(self.extensions)
        text = json.dumps(self.doc, separators=(',', ':'), allow_nan=False).encode('utf-8')
        text += b' ' * (-len(text) % 4)
        self.binary.extend(b'\0' * (-size % 4))
        length = 12+8+len(text)+8+len(self.binary)
        return (struct.pack('<4sII', b'glTF', 2, length) + struct.pack('<II', len(text), 0x4E4F534A)
                + text + struct.pack('<II', len(self.binary), 0x004E4942) + self.binary)


def _png(filename, settings=None):
    path = os.path.expandvars(os.path.expanduser(filename))
    if not os.path.isabs(path):
        path = cmds.workspace(expandName=path)
    if settings:
        from ..texture_tools import prepare_image
        image = prepare_image(path, **settings)
    else:
        image = QImage(path)
    if image.isNull():
        raise ValueError('Cannot load diffuse texture: '+path)
    data = QByteArray()
    stream = QBuffer(data)
    stream.open(QIODevice.WriteOnly)
    if not image.save(stream, 'PNG'):
        raise ValueError('Cannot encode texture: '+path)
    stream.close()
    return bytes(data), image.width(), image.height(), image.hasAlphaChannel()


def _fast64_extras(mode, color, layer, cull, texture=None):
    textured = mode in ('TextureShaded', 'TextureVertexColor')
    primitive = mode == 'Primitive'
    if textured:
        cc = {'A': 1, 'B': 8, 'C': 4, 'D': 7, 'A_alpha': 1, 'B_alpha': 7, 'C_alpha': 4, 'D_alpha': 7}
    else:
        cc = {'A': 8, 'B': 8, 'C': 16, 'D': 3 if primitive else 4,
              'A_alpha': 7, 'B_alpha': 7, 'C_alpha': 7, 'D_alpha': 3 if primitive else 4}
    data = {'combiner1': dict(cc), 'combiner2': dict(cc), 'set_combiner': 1,
            'set_prim': int(primitive), 'prim_color': list(color), 'set_env': 0, 'env_color': [1,1,1,1],
            'set_blend': 0, 'blend_color': [0,0,0,0.5],
            'draw_layer': {'sm64': {'Opaque': 1, 'Cutout': 4, 'Transparent': 5}[layer], 'oot': 0},
            'rdp_settings': {'g_mdsft_cycletype': 0, 'g_cull_back': int(cull), 'g_cull_front': 0,
                             'g_lighting': int(mode in ('Shaded', 'TextureShaded')), 'g_shade': 1,
                             'g_shade_smooth': 1, 'g_zbuffer': 1, 'g_fog': 0, 'g_tex_gen': 0,
                             'g_mdsft_text_filt': 2, 'set_rendermode': 0,
                             'g_mdsft_alpha_compare': 1 if layer == 'Cutout' else 0}}
    if texture:
        name, width, height, wrap_s, wrap_t = texture
        def axis(size, wrap):
            return {'clamp': int(not wrap), 'mirror': 0, 'low': 0, 'high': size-1,
                    'mask': int(math.log2(size)) if size > 0 and not size & (size-1) else 0, 'shift': 0}
        data['tex0'] = {'tex_set': 1, 'use_tex_reference': 0, 'tex': {'name': name, 'type': 'Image'},
                        'tex_format': 0, 'S': axis(width, wrap_s), 'T': axis(height, wrap_t)}
    return {'is_f3d': 1, 'mat_ver': 6, 'f3d_mat': data}


def _material(builder, shader, output, f3d, external, usage=None):
    modes = ('VertexColor', 'TextureVertexColor', 'Primitive', 'Shaded', 'TextureShaded')
    native = bool(_attr(shader, 'fast64Material', False))
    if f3d and not native:
        raise ValueError(shader+': convert this material to F3D before F3D export')
    mode = modes[int(_attr(shader, 'fast64Mode', 3))] if native else 'Shaded'
    layers = ('Opaque', 'Transparent', 'Cutout')
    layer = layers[int(_attr(shader, 'fast64Layer', 0))]
    color = tuple(_attr(shader, 'color', _attr(shader, 'baseColor', (1,1,1))))
    tint = _attr(shader, 'fast64Tint', '')
    if tint:
        color = tuple(json.loads(tint))
    alpha = _attr(shader, 'fast64Opacity', 1-_attr(shader, 'transparency', (0,0,0))[0])
    color = tuple(_clamp(c) for c in color) + (_clamp(alpha),)
    cull = bool(_attr(shader, 'fast64CullBack', True))
    mat = {'name': shader, 'doubleSided': not cull,
           'pbrMetallicRoughness': {'baseColorFactor': list(color) if mode == 'Primitive' else [1,1,1,1],
                                   'metallicFactor': 0, 'roughnessFactor': 1},
           'alphaMode': {'Opaque':'OPAQUE', 'Transparent':'BLEND', 'Cutout':'MASK'}[layer]}
    if layer == 'Cutout':
        mat['alphaCutoff'] = 0.5
    image_info = None
    decal = bool(_attr(shader,'fast64AlphaOverVertex',False))
    bits = 32 if _attr(shader,'fast64TextureBits',0) else 16
    crop = json.loads(_attr(shader,'fast64TextureCrop','') or 'null') if f3d else None
    crop_reason='Manual crop' if crop else 'Automatic cropping disabled'
    alternate = _attr(shader,'fast64AlternateTexture','')
    texture_path = _attr(shader, 'fast64Texture', '')
    if mode in ('TextureShaded', 'TextureVertexColor'):
        from ..texture_tools import source_image, automatic_crop
        original=source_image(texture_path)
        if crop and alternate:
            other=source_image(alternate)
            if (original.width(),original.height())!=(other.width(),other.height()):
                raise ValueError('Animation texture sources must have the same dimensions for shared cropping')
        if f3d and not crop and _attr(shader,'fast64AutoCrop',False):
            if _attr(shader,'fast64WrapU',True) or _attr(shader,'fast64WrapV',True):
                crop_reason='Skipped: wrapping enabled; clamp both axes to allow safe cropping'
            else:
                crop,crop_reason=automatic_crop([texture_path]+([alternate] if alternate else []),
                    (usage or {}).get('bounds'),decal,int(_attr(shader,'fast64TextureMax',64)))
        settings = dict(bits=bits, maximum=int(_attr(shader,'fast64TextureMax',64)),
                        resize=bool(_attr(shader,'fast64AutoResize',False)),crop=crop) if f3d else None
        png, width, height, has_alpha = _png(texture_path,settings)
        wrap_s, wrap_t = bool(_attr(shader, 'fast64WrapU', True)), bool(_attr(shader, 'fast64WrapV', True))
        index = len(builder.doc.setdefault('images', []))
        if f3d:
            texture_name = os.path.splitext(os.path.basename(output))[0]+'_tex_'+hashlib.sha256(png).hexdigest()[:12]+'.png'
            external[texture_name] = png
            image = {'uri': quote(texture_name), 'name': texture_name}
        else:
            texture_name = os.path.basename(texture_path)
            image = {'bufferView': builder.view(png), 'mimeType':'image/png', 'name':texture_name}
        builder.doc['images'].append(image)
        sampler = {'magFilter':9729, 'minFilter':9729, 'wrapS':10497 if wrap_s else 33071, 'wrapT':10497 if wrap_t else 33071}
        if f3d:
            builder.extensions.add('FAST64_sampler_n64')
            sampler['extensions'] = {'FAST64_sampler_n64': {'set':True, 'format':{'texture':'RGBA','size':bits},
                                     'fields':[{'clamp':not wrap_s,'mirror':False}, {'clamp':not wrap_t,'mirror':False}]}}
        sampler_index = len(builder.doc.setdefault('samplers', [])); builder.doc['samplers'].append(sampler)
        texture_index = len(builder.doc.setdefault('textures', []))
        builder.doc['textures'].append({'sampler':sampler_index, 'source':index})
        mat['pbrMetallicRoughness']['baseColorTexture'] = {'index':texture_index}
        image_info = (texture_name, width, height, wrap_s, wrap_t)
        affected=(usage or {}).get('meshes',[])
        qc={'material':shader,'sourceTextures':[texture_path]+([alternate] if alternate else []),
            'sourceSize':[original.width(),original.height()],'exportSize':[width,height],
            'cropped':bool(crop),'cropPixels':crop,'reason':crop_reason,
            'uvsAdjusted':bool(crop),'affectedMeshes':affected if crop else [],
            'uvBoundsBefore':(usage or {}).get('bounds'),'sourceUVsChanged':False,
            'format':'RGBA'+str(bits),'tmemBytes':width*height*(bits//8)}
        qc['exportedTextures']=[texture_name]
        builder.doc.setdefault('extras',{}).setdefault('textureQC',[]).append(qc)
    if mode in ('VertexColor', 'TextureVertexColor', 'Primitive'):
        mat.setdefault('extensions', {})['KHR_materials_unlit'] = {}
        builder.extensions.add('KHR_materials_unlit')
    if native:
        mat['extras'] = _fast64_extras(mode, color, layer, cull, image_info)
        if image_info:
            mat['extras']['f3d_mat']['tex0']['tex_format'] = 1 if bits==32 else 0
        if decal:
            cc={'A':1,'B':4,'C':8,'D':4,'A_alpha':7,'B_alpha':7,'C_alpha':7,'D_alpha':4}
            mat['extras']['f3d_mat'].update(combiner1=dict(cc),combiner2=dict(cc))
        # Both legacy Fast64 custom properties and Fast64 extensions are emitted.
        if mode in ('TextureShaded', 'TextureVertexColor'):
            cc_color, cc_alpha = ['TEXEL0','0','SHADE','0'], ['TEXEL0','0','SHADE','0']
        else:
            term = 'PRIMITIVE' if mode == 'Primitive' else 'SHADE'
            cc_color, cc_alpha = ['0','0','0',term], ['0','0','0',term]
        if decal:
            cc_color,cc_alpha=['TEXEL0','SHADE','TEXEL0_ALPHA','SHADE'],['0','0','0','SHADE']
        n64 = {'combiner':{'set':True, 'cycles':[{'color':cc_color, 'alpha':cc_alpha}]},
               'otherModes':{'textureFilter':'BILERP', 'perspectiveCorrection':'PERSP'}, 'textures':{},
               'extensions':{'FAST64_materials_f3d':{'geometryMode':{
                   'zBuffer':True,'shade':True,'cullBack':cull,'lighting':mode in ('Shaded','TextureShaded'),'shadeSmooth':True}}}}
        if mode == 'Primitive':
            n64['primitive'] = {'set':True, 'color':list(color)}
        if image_info:
            n64['textures']['0'] = {'index':texture_index}
        mat.setdefault('extensions', {})['FAST64_materials_n64'] = n64
        builder.extensions.update(('FAST64_materials_n64','FAST64_materials_f3d'))
    builder.doc['materials'].append(mat)
    vertex_tint = (1,1,1,color[3]) if mode in ('VertexColor','TextureVertexColor') else color
    if f3d and image_info and alternate:
        alternate_png,aw,ah,_ = _png(alternate,settings)
        if (aw,ah)!=(width,height):
            raise ValueError('Animation textures must share dimensions')
        alternate_name=os.path.splitext(os.path.basename(output))[0]+'_tex_'+hashlib.sha256(alternate_png).hexdigest()[:12]+'.png'
        external[alternate_name]=alternate_png
        qc['exportedTextures'].append(alternate_name)
        mat['extras']['mayaTextureAnimation']={'open':texture_name,'closed':alternate_name,'cycleFrames':24}
    crop_uv=None
    if crop:
        original=source_image(texture_path)
        crop_uv=(crop[0]/original.width(),1-(crop[1]+crop[3])/original.height(),crop[2]/original.width(),crop[3]/original.height())
        qc['uvTransform']={'offset':[crop[0]/original.width(),crop[1]/original.height()],
                           'scale':[original.width()/crop[2],original.height()/crop[3]],
                           'convention':'glTF UV, after Maya V flip and material placement'}
    return len(builder.doc['materials'])-1, {'mode':mode, 'color':vertex_tint,'crop':crop_uv,
        'repeat':(_attr(shader,'fast64RepeatU',1),_attr(shader,'fast64RepeatV',1)),
        'offset':(_attr(shader,'fast64OffsetU',0),_attr(shader,'fast64OffsetV',0))}


def export_glb(path, selected=True, f3d=True):
    path = os.path.abspath(path)
    if not path.lower().endswith('.glb'):
        path += '.glb'
    if os.path.exists(path):
        raise ValueError('Output already exists; choose another GLB filename: '+path)
    objects = cmds.ls(selection=True, long=True, objectsOnly=True) if selected else cmds.ls(assemblies=True,long=True)
    meshes = cmds.ls(objects, dag=True, type='mesh', noIntermediate=True, long=True) if objects else []
    meshes = list(OrderedDict.fromkeys(meshes or []))
    if not meshes:
        raise ValueError('Select the polygon mesh to export')
    builder, external, material_map = Builder(), {}, {}
    # Include every exported face before choosing a shared crop for a material.
    usage={}
    for mesh in meshes:
        sl=om.MSelectionList();sl.add(mesh);dag=sl.getDagPath(0);fn=om.MFnMesh(dag)
        groups,indices=fn.getConnectedShaders(dag.instanceNumber())
        uvsets=fn.getUVSetNames();uvset='UVMap' if 'UVMap' in uvsets else fn.currentUVSetName() if uvsets else None
        polygon=om.MItMeshPolygon(dag)
        local={}
        local_uvs={}
        while not polygon.isDone():
            face=polygon.index();index=indices[face]
            if index<0:raise ValueError(mesh+': face has no material')
            group=om.MFnDependencyNode(groups[index]).name()
            shader=(cmds.listConnections(group+'.surfaceShader',s=True,d=False) or [None])[0]
            if not shader:raise ValueError(group+': no surface shader')
            item=usage.setdefault(shader,{'bounds':None,'meshes':[]})
            local.setdefault(shader,[]).append(face)
            if uvset:
                for corner in range(len(polygon.getVertices())):
                    try:
                        u,v=polygon.getUV(corner,uvset)
                        local_uvs.setdefault(shader,set()).add(polygon.getUVIndex(corner,uvset))
                    except RuntimeError:continue
                    u=u*_attr(shader,'fast64RepeatU',1)+_attr(shader,'fast64OffsetU',0)
                    v=1-(v*_attr(shader,'fast64RepeatV',1)+_attr(shader,'fast64OffsetV',0))
                    b=item['bounds'];item['bounds']=[min(b[0],u),min(b[1],v),max(b[2],u),max(b[3],v)] if b else [u,v,u,v]
            polygon.next()
        for shader,faces in local.items():
            usage[shader]['meshes'].append({'mesh':mesh,'uvSet':uvset,'faces':faces,
                'sourceUVIndices':sorted(local_uvs.get(shader,[]))})
    z_up = cmds.upAxis(query=True, axis=True) == 'z'
    triangle_count = vertex_count = 0
    def axis(v, scale=1):
        values = (v.x,v.z,-v.y) if z_up else (v.x,v.y,v.z)
        result = tuple(c*scale for c in values)
        if any(not math.isfinite(c) for c in result):
            raise ValueError('Non-finite mesh coordinates')
        return result
    for mesh in meshes:
        sl = om.MSelectionList(); sl.add(mesh); dag=sl.getDagPath(0); fn=om.MFnMesh(dag)
        shading_groups, assignments = fn.getConnectedShaders(dag.instanceNumber())
        color_sets = fn.getColorSetNames()
        color_source = 'Col' if 'Col' in color_sets else (fn.currentColorSetName() if color_sets else None)
        colors = fn.getFaceVertexColors(color_source, om.MColor((1,1,1,1))) if color_source else None
        alphas = fn.getFaceVertexColors('Alpha', om.MColor((1,1,1,1))) if color_source=='Col' and 'Alpha' in color_sets else None
        uvsets = fn.getUVSetNames()
        uvset = 'UVMap' if 'UVMap' in uvsets else fn.currentUVSetName() if uvsets else None
        groups = OrderedDict()
        mirrored = dag.inclusiveMatrix().det4x4() < 0
        polygon = om.MItMeshPolygon(dag)
        while not polygon.isDone():
            face = polygon.index(); group_index=assignments[face]
            if group_index < 0:
                raise ValueError(mesh+': face has no material')
            group = om.MFnDependencyNode(shading_groups[group_index]).name()
            shaders=cmds.listConnections(group+'.surfaceShader', source=True, destination=False) or []
            if len(shaders)!=1:
                raise ValueError(group+': no single surface shader')
            shader=shaders[0]
            if shader not in material_map:
                material_map[shader]=_material(builder,shader,path,f3d,external,usage.get(shader))
            material_index, settings=material_map[shader]
            storage=groups.setdefault(material_index, {'values':[], 'lookup':{}, 'indices':[]})
            points, ids=polygon.getTriangles(om.MSpace.kWorld)
            face_vertices=list(polygon.getVertices())
            for start in range(0,len(ids),3):
                corners=[]
                for i in range(start,start+3):
                    vid=ids[i]; index=fn.getFaceVertexIndex(face,vid,False)
                    c=colors[index] if colors is not None else om.MColor((1,1,1,1))
                    alpha=alphas[index].r if alphas is not None else c.a
                    rgba=tuple(_clamp(v) for v in (c.r,c.g,c.b,alpha))
                    if settings['mode']!='Primitive':
                        rgba=tuple(rgba[j]*settings['color'][j] for j in range(4))
                    normal=fn.getFaceVertexNormal(face,vid,om.MSpace.kWorld); normal.normalize()
                    uv=(0,0)
                    if uvset:
                        try:
                            u,v=polygon.getUV(face_vertices.index(vid),uvset)
                            uv=(u*settings['repeat'][0]+settings['offset'][0],1-(v*settings['repeat'][1]+settings['offset'][1]))
                            if settings.get('crop'):
                                x,y,w,h=settings['crop']
                                uv=((uv[0]-x)/w,(uv[1]-(1-y-h))/h)
                        except RuntimeError:
                            if settings['mode'] in ('TextureShaded','TextureVertexColor'):
                                raise ValueError(mesh+': textured face is missing UVs')
                    elif settings['mode'] in ('TextureShaded','TextureVertexColor'):
                        raise ValueError(mesh+': textured material requires UVs')
                    value=axis(points[i],0.01)+axis(normal)+uv+rgba
                    if any(not math.isfinite(v) for v in value):
                        raise ValueError(mesh+': invalid vertex data')
                    if value not in storage['lookup']:
                        storage['lookup'][value]=len(storage['values']); storage['values'].append(value)
                    corners.append(storage['lookup'][value])
                if mirrored:
                    corners.reverse()
                storage['indices'].extend(corners)
                triangle_count+=1
            polygon.next()
        primitives=[]
        for material_index, storage in groups.items():
            values=storage['values']; vertex_count+=len(values)
            if not values:
                continue
            attrs={'POSITION':builder.accessor([v[:3] for v in values],'VEC3',bounds=True),
                   'NORMAL':builder.accessor([v[3:6] for v in values],'VEC3'),
                   'COLOR_0':builder.accessor([v[8:12] for v in values],'VEC4')}
            if uvset:
                attrs['TEXCOORD_0']=builder.accessor([v[6:8] for v in values],'VEC2')
            index_component=5123 if len(values)<=65536 else 5125
            indices=builder.accessor([(v,) for v in storage['indices']],'SCALAR',index_component,34963)
            primitives.append({'attributes':attrs,'indices':indices,'mode':4,'material':material_index})
        if not primitives:
            continue
        name=mesh.split('|')[-1]
        mesh_index=len(builder.doc['meshes']); builder.doc['meshes'].append({'name':name,'primitives':primitives,
            'extras':{'mayaColorSets':list(color_sets),'mayaUVSet':uvset or ''}})
        node_index=len(builder.doc['nodes']); builder.doc['nodes'].append({'name':name,'mesh':mesh_index})
        builder.doc['scenes'][0]['nodes'].append(node_index)
    if not triangle_count:
        raise ValueError('No triangles to export')
    payload=builder.finish()
    directory=os.path.dirname(path); os.makedirs(directory,exist_ok=True)
    # Validate all geometry/materials before publishing any output.
    qc=builder.doc.get('extras',{}).get('textureQC',[])
    report_path=path+'.texture-qc.json'
    report={'glb':path,'textures':qc,'sourceUVsChanged':False}
    files=[(os.path.join(directory,name),data) for name,data in external.items()]+[(report_path,(json.dumps(report,indent=2)+'\n').encode()),(path,payload)]
    for output,data in files:
        if os.path.exists(output):
            with open(output,'rb') as stream:
                if stream.read()!=data:
                    raise ValueError('Existing output differs: '+output)
    for output,data in files:
        if os.path.exists(output):
            continue
        fd,temporary=tempfile.mkstemp(dir=directory,suffix='.tmp')
        try:
            with os.fdopen(fd,'wb') as stream:
                stream.write(data)
            os.replace(temporary,output)
        finally:
            if os.path.exists(temporary): os.unlink(temporary)
    return {'path':path,'meshes':len(builder.doc['meshes']),'materials':len(builder.doc['materials']),
            'triangles':triangle_count,'vertices':vertex_count,'external_textures':list(external),
            'textureQC':qc,'textureQCReport':report_path}
