"""Non-destructive N64 texture preparation and alpha decals over painted colors."""
import json
import math
import os
import maya.cmds as cmds
from PySide2.QtGui import QImage
from PySide2.QtCore import Qt
from . import scene

def source_image(path):
    path=os.path.expandvars(os.path.expanduser(path))
    if not os.path.isabs(path):path=cmds.workspace(expandName=path)
    image=QImage(path)
    if image.isNull():raise ValueError('Cannot load texture: '+path)
    return image

def automatic_crop(paths, uv_bounds, decal=False, maximum=32):
    """One shared pixel rectangle; alpha decals may have UVs outside that rectangle."""
    images=[source_image(path) for path in paths]
    width,height=images[0].width(),images[0].height()
    if any((im.width(),im.height())!=(width,height) for im in images):
        raise ValueError('Animation texture sources must have the same dimensions for shared cropping')
    if decal:
        bounds=[]
        for image in images:
            if not image.hasAlphaChannel():return None,'Skipped: decal image has no alpha channel'
            rgba=image.convertToFormat(QImage.Format_RGBA8888)
            buffer=rgba.constBits()
            raw=buffer.asstring(rgba.byteCount()) if hasattr(buffer,'asstring') else bytes(buffer)
            alpha=raw[3::4]
            x0,y0,x1,y1=width,height,-1,-1
            for index,value in enumerate(alpha):
                if value:
                    x,y=index%width,index//width
                    x0=min(x0,x);y0=min(y0,y);x1=max(x1,x);y1=max(y1,y)
            if x1>=0:bounds.append((x0,y0,x1+1,y1+1))
        if not bounds:return None,'Skipped: all animation states are fully transparent'
        x0=min(b[0] for b in bounds);y0=min(b[1] for b in bounds)
        x1=max(b[2] for b in bounds);y1=max(b[3] for b in bounds)
        padding=max(2,int(math.ceil(max(x1-x0,y1-y0)/max(1,maximum)))*2)
        # A transparent border is required so clamping outside the crop is safe.
        if x0<padding or y0<padding or x1+padding>width or y1+padding>height:
            return None,'Skipped: insufficient transparent border for safe decal clamping'
        reason='Shared alpha bounds of all animation states'
    else:
        if not uv_bounds:return None,'Skipped: no UV bounds'
        u0,v0,u1,v1=uv_bounds
        if u0<0 or v0<0 or u1>1 or v1>1:return None,'Skipped: UVs extend beyond the source image'
        x0=math.floor(u0*width);y0=math.floor(v0*height)
        x1=math.ceil(u1*width);y1=math.ceil(v1*height)
        padding=2;reason='Bounds of UVs used by exported faces'
    x0=max(0,x0-padding);y0=max(0,y0-padding)
    x1=min(width,x1+padding);y1=min(height,y1+padding)
    if (x1-x0)*(y1-y0)>=width*height*.9:
        return None,'Skipped: crop would save less than 10% of source area'
    return [x0,y0,max(1,x1-x0),max(1,y1-y0)],reason

def prepare_image(path, bits=16, maximum=64, resize=True, crop=None):
    if bits not in (16,32) or maximum not in (4,8,16,32,64,128,256):
        raise ValueError('Choose RGBA16/RGBA32 and a power-of-two maximum dimension from 4 to 256')
    path = os.path.expandvars(os.path.expanduser(path))
    if not os.path.isabs(path):
        path = cmds.workspace(expandName=path)
    image = QImage(path)
    if image.isNull():
        raise ValueError('Cannot load texture: '+path)
    if crop:
        if len(crop)!=4 or any(not isinstance(v,int) for v in crop) or crop[0]<0 or crop[1]<0 or crop[2]<1 or crop[3]<1 or crop[0]+crop[2]>image.width() or crop[1]+crop[3]>image.height():
            raise ValueError('Crop must be an integer rectangle within the source image')
        image = image.copy(*crop)
    if resize:
        w = max(4, 2**int(math.log2(min(image.width(), maximum))))
        h = max(1, 2**int(math.log2(min(image.height(), maximum))))
        while w*h*(bits//8)>4096:
            if w>=h and w>4: w//=2
            else: h//=2
        # Premultiplied filtering avoids dark fringes at transparent edges.
        image = image.convertToFormat(QImage.Format_ARGB32_Premultiplied).scaled(w,h,Qt.IgnoreAspectRatio,Qt.SmoothTransformation).convertToFormat(QImage.Format_RGBA8888)
    w,h=image.width(),image.height()
    if w<4 or any(s<1 or s>256 or s&(s-1) for s in (w,h)) or w*h*(bits//8)>4096:
        raise ValueError('Texture must have power-of-two dimensions and fit 4096 bytes of N64 TMEM; enable automatic resizing')
    return image

def configure(shader, bits=32, maximum=32, resize=True, decal=True, alternate=None, crop=None, auto_crop=False):
    if not scene.attr(shader,'fast64Material',False):
        raise ValueError('Convert the material to F3D first')
    scene.add(shader,'fast64TextureBits',0 if bits==16 else 1,'enum',('RGBA16','RGBA32'))
    scene.add(shader,'fast64TextureMax',maximum,'long')
    scene.add(shader,'fast64AutoResize',resize,'bool')
    scene.add(shader,'fast64AlphaOverVertex',decal,'bool')
    scene.add(shader,'fast64AutoCrop',auto_crop,'bool')
    if alternate is not None:scene.add(shader,'fast64AlternateTexture',alternate)
    if crop is not None:scene.add(shader,'fast64TextureCrop',json.dumps(crop) if crop else '')
    if decal:
        cmds.setAttr(shader+'.fast64Mode',1)
        cmds.setAttr(shader+'.fast64Layer',0)
        scene.add(shader,'fast64Opacity',1.0,'double')
        for plug in cmds.listConnections(shader+'.transparency',s=True,d=False,p=True) or []:
            cmds.disconnectAttr(plug,shader+'.transparency')
        cmds.setAttr(shader+'.transparency',0,0,0,type='double3')
        files=cmds.listConnections(shader+'.color',s=True,d=False,type='file') or []
        if files:
            # White over black in CPV Modulate produces the actual vertex RGB.
            layer=cmds.shadingNode('layeredTexture',asTexture=True,name=shader+'_vertexDecal',skipSelect=True)
            cmds.setAttr(layer+'.inputs[2].color',0,0,0,type='double3')
            cmds.setAttr(layer+'.inputs[2].alpha',1)
            cmds.setAttr(layer+'.inputs[2].blendMode',0)
            cmds.setAttr(layer+'.inputs[1].color',1,1,1,type='double3')
            cmds.setAttr(layer+'.inputs[1].alpha',1)
            cmds.setAttr(layer+'.inputs[1].blendMode',13)
            cmds.connectAttr(files[0]+'.outColor',layer+'.inputs[0].color')
            cmds.connectAttr(files[0]+'.outAlpha',layer+'.inputs[0].alpha')
            cmds.setAttr(layer+'.inputs[0].blendMode',1)
            cmds.connectAttr(layer+'.outColor',shader+'.color',f=True)
    return shader

def configure_selected(bits=32, maximum=32, resize=True, decal=True, auto_crop=True):
    shaders=set()
    for mesh in scene.selected_meshes():
        dag,fn=scene.mesh_fn(mesh)
        for sg in fn.getConnectedShaders(dag.instanceNumber())[0]:
            import maya.api.OpenMaya as om
            shaders.add(scene.shader_for_group(om.MFnDependencyNode(sg).name()))
    cmds.undoInfo(openChunk=True,chunkName='Configure N64 textures')
    try:
        return [configure(s,bits,maximum,resize,decal,auto_crop=auto_crop) for s in shaders if scene.attr(s,'fast64Texture','')]
    finally:
        cmds.undoInfo(closeChunk=True)
