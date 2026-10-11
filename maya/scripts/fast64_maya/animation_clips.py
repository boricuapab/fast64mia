"""Named frame ranges saved on the selected mesh transform."""
import json
import maya.cmds as cmds
from . import scene
from .vendor.animated_export import validate_clips


def show():
    meshes = scene.selected_meshes()
    if len(meshes) != 1:
        raise ValueError('Select one character mesh')
    parent = cmds.listRelatives(meshes[0], parent=True, fullPath=True)[0]
    clips = None
    if cmds.attributeQuery('fast64AnimationClips', node=parent, exists=True):
        raw = cmds.getAttr(parent + '.fast64AnimationClips')
        clips = json.loads(raw) if raw else None
    clips = clips or [{'name':'animation_00', 'start':cmds.playbackOptions(query=True,min=True), 'end':cmds.playbackOptions(query=True,max=True)}]
    if cmds.window('fast64AnimationClipsWindow', exists=True):
        cmds.deleteUI('fast64AnimationClipsWindow')
    window = cmds.window('fast64AnimationClipsWindow', title='Fast64 Animation Clips', widthHeight=(430,260))
    cmds.columnLayout(adjustableColumn=True, rowSpacing=8)
    cmds.text(label='Author each animation on the same rig at a different frame range.', align='left')
    cmds.text(label='One clip per line: name, start frame, end frame', align='left')
    field = cmds.scrollField(wordWrap=False, height=130, text='\n'.join('{name}, {start:g}, {end:g}'.format(**clip) for clip in clips))

    def save(*_):
        try:
            values = []
            for line in cmds.scrollField(field,query=True,text=True).splitlines():
                if not line.strip():
                    continue
                name,start,end = [value.strip() for value in line.split(',')]
                values.append({'name':name, 'start':float(start), 'end':float(end)})
            values = validate_clips(values)
            cmds.undoInfo(openChunk=True,chunkName='Set Fast64 animation clips')
            try:
                if not cmds.attributeQuery('fast64AnimationClips',node=parent,exists=True):
                    cmds.addAttr(parent,longName='fast64AnimationClips',dataType='string')
                cmds.setAttr(parent+'.fast64AnimationClips',json.dumps(values),type='string')
            finally:
                cmds.undoInfo(closeChunk=True)
            cmds.deleteUI(window)
        except (ValueError, KeyError) as error:
            cmds.warning(str(error))

    cmds.button(label='Save Clip Ranges',command=save)
    cmds.showWindow(window)
    return window
