"""Run with Maya 2023 mayapy; uses a separate, disposable standalone scene."""
import json
import os
import re
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "scripts"))
import maya.standalone
maya.standalone.initialize(name="python")
import maya.cmds as cmds
import maya.api.OpenMaya as om
from PySide2.QtGui import QImage, QColor
from fast64_maya import core, scene, paint


class NativeTests(unittest.TestCase):
    def test_automatic_crop_shared_states_uv_remap_and_qc(self):
        from fast64_maya import texture_tools
        from fast64_maya.vendor import glb_export
        import struct
        with tempfile.TemporaryDirectory() as temp:
            files=[]
            for state in range(2):
                im=QImage(256,256,QImage.Format_RGBA8888);im.fill(QColor(0,0,0,0))
                for y in range(80,144):
                    for x in range(80,144+state*32):im.setPixelColor(x,y,QColor(200,80,40,255))
                filename=os.path.join(temp,str(state)+'.png');im.save(filename);files.append(filename)
            crop,reason=texture_tools.automatic_crop(files,None,True,32)
            self.assertEqual(crop,[74,74,108,76])
            shader,group=scene.new_material('AutoEyes')
            cmds.setAttr(shader+'.fast64Texture',files[0],type='string')
            texture_tools.configure(shader,32,32,True,True,files[1],auto_crop=True)
            cmds.setAttr(shader+'.fast64WrapU',False);cmds.setAttr(shader+'.fast64WrapV',False)
            cmds.polyPlane(subdivisionsX=1,subdivisionsY=1);cmds.sets('pPlane1',e=True,forceElement=group)
            cmds.select('pPlane1')
            before=cmds.polyEditUV('pPlane1.map[*]',q=True)
            result=glb_export.export_glb(os.path.join(temp,'auto.glb'))
            self.assertEqual(before,cmds.polyEditUV('pPlane1.map[*]',q=True))
            qc=result['textureQC'][0];self.assertEqual(qc['cropPixels'],crop)
            self.assertEqual(qc['affectedMeshes'][0]['faces'],[0]);self.assertTrue(qc['uvsAdjusted'])
            self.assertEqual(qc['affectedMeshes'][0]['sourceUVIndices'],[0,1,2,3])
            with open(result['textureQCReport']) as f:self.assertEqual(json.load(f)['textures'],result['textureQC'])
            with open(result['path'],'rb') as f:raw=f.read()
            size=struct.unpack_from('<I',raw,12)[0];doc=json.loads(raw[20:20+size]);binary=raw[28+size:]
            index=doc['meshes'][0]['primitives'][0]['attributes']['TEXCOORD_0']
            accessor=doc['accessors'][index];view=doc['bufferViews'][accessor['bufferView']]
            coords=[struct.unpack_from('<ff',binary,view.get('byteOffset',0)+accessor.get('byteOffset',0)+i*8) for i in range(accessor['count'])]
            self.assertAlmostEqual(min(u for u,v in coords),-74/108,places=5)
            self.assertAlmostEqual(max(u for u,v in coords),(256-74)/108,places=5)
            self.assertEqual(len(result['external_textures']),2)
            cmds.setAttr(shader+'.fast64WrapU',True)
            skipped=glb_export.export_glb(os.path.join(temp,'wrapped.glb'))['textureQC'][0]
            self.assertFalse(skipped['cropped']);self.assertIn('wrapping',skipped['reason'])
            crop,_=texture_tools.automatic_crop(files,[.25,.25,.5,.5],False,32)
            self.assertEqual(crop,[62,62,68,68])
            im=QImage(16,16,QImage.Format_RGBA8888);im.fill(QColor(1,1,1,255));im.save(files[1])
            with self.assertRaises(ValueError):texture_tools.automatic_crop(files,None,True,32)

    def test_resized_alpha_decal_and_alternate_texture(self):
        from fast64_maya import texture_tools
        from fast64_maya.vendor import glb_export
        from pathlib import Path
        with tempfile.TemporaryDirectory() as temp:
            files=[]
            for index in range(2):
                image=QImage(256,256,QImage.Format_RGBA8888)
                image.fill(QColor(0,0,0,0))
                for y in range(80,144):
                    for x in range(80,144):image.setPixelColor(x,y,QColor(200,index*80,40,128))
                filename=os.path.join(temp,str(index)+'.png');image.save(filename);files.append(filename)
            cmds.polyPlane()
            source=cmds.shadingNode('blinn',asShader=True)
            group=cmds.sets(renderable=True,noSurfaceShader=True,empty=True)
            cmds.connectAttr(source+'.outColor',group+'.surfaceShader')
            cmds.sets('pPlane1',e=True,forceElement=group)
            file=cmds.shadingNode('file',asTexture=True)
            cmds.setAttr(file+'.fileTextureName',files[0],type='string')
            cmds.connectAttr(file+'.outColor',source+'.color')
            cmds.connectAttr(file+'.outTransparency',source+'.transparency')
            cmds.select('pPlane1');scene.convert_materials()
            texture_tools.configure(source+'_F3D',32,32,True,True,files[1],[72,72,80,80])
            self.assertEqual(cmds.ls(sl=True),['pPlane1'])
            result=glb_export.export_glb(os.path.join(temp,'decal.glb'))
            self.assertEqual(len(result['external_textures']),2)
            raw=Path(result['path']).read_bytes();length=int.from_bytes(raw[12:16],'little');doc=json.loads(raw[20:20+length])
            mat=doc['materials'][0]
            self.assertEqual(mat['alphaMode'],'OPAQUE')
            self.assertEqual(mat['extras']['f3d_mat']['combiner1']['C'],8)
            self.assertEqual(mat['extensions']['FAST64_materials_n64']['combiner']['cycles'][0]['color'],['TEXEL0','SHADE','TEXEL0_ALPHA','SHADE'])
            for name in result['external_textures']:
                im=QImage(os.path.join(temp,name));self.assertEqual((im.width(),im.height()),(32,32))
                self.assertEqual(im.pixelColor(0,0).alpha(),0)
                self.assertAlmostEqual(im.pixelColor(16,16).alpha(),128,delta=2)
            with self.assertRaises(ValueError):texture_tools.prepare_image(files[0],32,32,False)

    @classmethod
    def setUpClass(cls):
        cmds.loadPlugin(os.path.join(HERE, "plug-ins", "fast64Maya.py"))

    def setUp(self):
        cmds.file(new=True, force=True)
        cmds.undoInfo(state=True)
        cmds.upAxis(axis="y")

    def assigned(self, kind="blinn"):
        mesh = cmds.polyCube(name="TestMesh", width=2, height=2, depth=2, constructionHistory=False)[0]
        shader = cmds.shadingNode(kind, asShader=True)
        sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=shader + "SG")
        cmds.connectAttr(shader + ".outColor", sg + ".surfaceShader")
        cmds.setAttr(shader + ".color", 0.8, 0.4, 0.2, type="double3")
        cmds.setAttr(shader + ".diffuse", 0.75)
        cmds.sets(mesh, edit=True, forceElement=sg)
        cmds.select(mesh)
        return mesh, shader, sg

    def test_blinn_phong_lambert_conversion_and_undo(self):
        for kind in ("blinn", "phong", "phongE", "lambert"):
            cmds.file(new=True, force=True)
            mesh, original, old_sg = self.assigned(kind)
            result = scene.convert_materials()
            new = result["converted"][0]
            self.assertEqual(cmds.nodeType(new), "lambert")
            self.assertEqual(scene.MODES[cmds.getAttr(new + ".fast64Mode")], "Shaded")
            color = cmds.getAttr(new + ".color")[0]
            for actual, expected in zip(color, (0.6, 0.3, 0.15)):
                self.assertAlmostEqual(actual, expected, places=5)
            self.assertTrue(cmds.objExists(original))
            groups = scene.gather()
            source, _, stats = core.display_list("cube", groups)
            self.assertEqual(stats["triangles"], 12)
            self.assertIn("gsSPSetLights1", source)
            self.assertNotIn("{{{{", source)
            cmds.undo()
            self.assertFalse(cmds.objExists(new))
            dag, fn = scene.mesh_fn(scene.selected_meshes()[0])
            sgs, _ = fn.getConnectedShaders(dag.instanceNumber())
            self.assertEqual(om.MFnDependencyNode(sgs[0]).name(), old_sg)

    def test_per_face_materials_and_colors(self):
        mesh, shader, _ = self.assigned("phong")
        shader2 = cmds.shadingNode("blinn", asShader=True)
        sg2 = cmds.sets(renderable=True, noSurfaceShader=True, empty=True)
        cmds.connectAttr(shader2 + ".outColor", sg2 + ".surfaceShader")
        cmds.sets(mesh + ".f[0]", edit=True, forceElement=sg2)
        cmds.select(mesh)
        scene.convert_materials()
        self.assertEqual(len(scene.gather()), 2)
        paint.fill(rgb=(1, 1, 1), alpha=1)
        cmds.select(mesh + ".f[0]")
        paint.fill(rgb=(1, 0, 0), alpha=0.5)
        cmds.select(mesh)
        paint.use_for_export()
        groups = scene.gather()
        red = [c for g in groups for t in g["triangles"] for c in t if c["color"][1] == 0]
        self.assertEqual(len(red), 6)
        self.assertTrue(all(abs(c["color"][3] - 0.5) < 0.001 for c in red))
        self.assertTrue(cmds.getAttr(scene.selected_meshes()[0] + ".displayColors"))
        paint.display(False)
        self.assertFalse(cmds.getAttr(scene.selected_meshes()[0] + ".displayColors"))

    def test_texture_uv_and_rgba16(self):
        with tempfile.TemporaryDirectory() as directory:
            image = QImage(32, 32, QImage.Format_RGBA8888)
            image.fill(QColor(255, 0, 0, 255))
            filename = os.path.join(directory, "red.png")
            self.assertTrue(image.save(filename))
            mesh, shader, _ = self.assigned()
            file_node = cmds.shadingNode("file", asTexture=True, skipSelect=True)
            cmds.setAttr(file_node + ".fileTextureName", filename, type="string")
            cmds.connectAttr(file_node + ".outColor", shader + ".color")
            converted = scene.convert_materials()["converted"][0]
            self.assertEqual(os.path.normpath(cmds.getAttr(converted + ".fast64Texture")), os.path.normpath(filename))
            groups = scene.gather()
            source, _, stats = core.display_list("tex", groups)
            self.assertEqual(stats["triangles"], 12)
            self.assertIn("0xF801F801F801F801ULL", source)
            self.assertIn("gsDPLoadTextureBlock", source)
            self.assertIn("G_CC_MODULATERGBA", source)
            self.assertTrue(all("uv" in c for g in groups for t in g["triangles"] for c in t))
            result = scene.export(directory, "textured")
            self.assertTrue(os.path.isfile(os.path.join(result["directory"], "model.inc.c")))
            with self.assertRaisesRegex(ValueError, "already exists"):
                scene.export(directory, "textured")

    def test_microcode_batches(self):
        mesh = cmds.polyPlane(subdivisionsX=10, subdivisionsY=10, width=100, height=100)[0]
        cmds.select(mesh)
        scene.create_material()
        for microcode, limit in (("F3D", 16), ("F3DEX2", 32)):
            source, _, stats = core.display_list("grid", scene.gather(), microcode)
            self.assertEqual(stats["triangles"], 200)
            loads = re.findall(r"gsSPVertex\((\w+), (\d+), 0\)", source)
            self.assertGreater(len(loads), 1)
            self.assertTrue(all(int(count) <= limit for _, count in loads))
            current_count = 0
            for line in source.splitlines():
                load = re.search(r"gsSPVertex\(\w+, (\d+), 0\)", line)
                if load:
                    current_count = int(load.group(1))
                tri = re.search(r"gsSP1Triangle\((\d+), (\d+), (\d+), 0\)", line)
                if tri:
                    self.assertTrue(all(int(i) < current_count for i in tri.groups()))

    def test_collision_world_scale_and_z_up(self):
        mesh, _, _ = self.assigned()
        cmds.move(10, 20, 30, mesh)
        cmds.scale(-1, 1, 1, mesh)
        groups = scene.gather(2, collision_only=True)
        source, _, stats = core.collision("cube", groups)
        self.assertEqual(stats, {"vertices": 8, "triangles": 12})
        self.assertIn("COL_TRI_INIT(SURFACE_DEFAULT, 12)", source)
        positions = [c["position"] for g in groups for t in g["triangles"] for c in t]
        self.assertAlmostEqual(min(p[0] for p in positions), 18)
        cmds.upAxis(axis="z")
        z_groups = scene.gather(2, collision_only=True)
        z_positions = [c["position"] for g in z_groups for t in g["triangles"] for c in t]
        self.assertEqual(z_positions[0], (positions[0][0], positions[0][2], -positions[0][1]))

    def test_reject_invalid_data(self):
        mesh, shader, _ = self.assigned()
        with self.assertRaisesRegex(ValueError, "not an F3D material"):
            scene.gather()
        scene.convert_materials()
        with self.assertRaisesRegex(ValueError, "outside"):
            core.display_list("oversize", scene.gather(100000))
        with self.assertRaisesRegex(ValueError, "TMEM"):
            core.rgba16(64, 64, [])
        with self.assertRaisesRegex(ValueError, "power-of-two"):
            core.rgba16(30, 32, [])
        with self.assertRaisesRegex(ValueError, "positive"):
            scene.gather(-1)
        with self.assertRaisesRegex(RuntimeError, "interactive Maya"):
            paint.start()

    def test_conversion_rejects_network_without_scene_changes(self):
        mesh, shader, _ = self.assigned()
        checker = cmds.shadingNode("checker", asTexture=True, skipSelect=True)
        cmds.connectAttr(checker + ".outColor", shader + ".color")
        before = set(cmds.ls())
        with self.assertRaisesRegex(ValueError, "bake"):
            scene.convert_materials()
        self.assertEqual(before, set(cmds.ls()))
        self.assertEqual(cmds.ls(selection=True), [mesh])

    def test_opacity_and_texture_placement(self):
        with tempfile.TemporaryDirectory() as directory:
            mesh, shader, _ = self.assigned("phong")
            image = QImage(32, 32, QImage.Format_RGBA8888)
            image.fill(QColor(0, 255, 0, 255))
            filename = os.path.join(directory, "green.png")
            image.save(filename)
            texture = cmds.shadingNode("file", asTexture=True, skipSelect=True)
            placement = cmds.shadingNode("place2dTexture", asUtility=True, skipSelect=True)
            cmds.connectAttr(placement + ".outUV", texture + ".uvCoord")
            for attribute in ("repeatUV", "offset", "wrapU", "wrapV"):
                cmds.connectAttr(placement + "." + attribute, texture + "." + attribute)
            cmds.setAttr(placement + ".repeatUV", 2, 3, type="double2")
            cmds.setAttr(placement + ".offset", 0.25, 0.5, type="double2")
            cmds.setAttr(placement + ".wrapU", False)
            cmds.setAttr(texture + ".fileTextureName", filename, type="string")
            cmds.connectAttr(texture + ".outColor", shader + ".color")
            cmds.setAttr(shader + ".transparency", 0.25, 0.25, 0.25, type="double3")
            converted = scene.convert_materials()["converted"][0]
            self.assertAlmostEqual(cmds.getAttr(converted + ".fast64Opacity"), 0.75)
            groups = scene.gather()
            self.assertEqual(groups[0]["material"]["repeat"], (2, 3))
            self.assertEqual(groups[0]["material"]["offset"], (0.25, 0.5))
            source, _, _ = core.display_list("opacity", groups)
            self.assertIn("G_RM_AA_ZB_XLU_SURF", source)
            self.assertIn("G_TX_CLAMP, G_TX_WRAP", source)

    def test_bundled_glb_export_without_importer(self):
        import struct
        from fast64_maya import attributes
        from fast64_maya.vendor import glb_export
        mesh, shader, _ = self.assigned()
        scene.convert_materials()
        paint.fill(rgb=(0.2, 0.4, 0.8), alpha=0.5)
        paint.use_for_export()
        attributes.prepare()
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "cube.glb")
            stats = glb_export.export_glb(path)
            self.assertEqual(stats["triangles"], 12)
            with open(path, "rb") as stream:
                payload = stream.read()
            magic, version, length = struct.unpack_from("<4sII", payload)
            self.assertEqual((magic, version, length), (b"glTF", 2, len(payload)))
            json_length = struct.unpack_from("<I", payload, 12)[0]
            doc = json.loads(payload[20:20 + json_length])
            primitive = doc["meshes"][0]["primitives"][0]
            self.assertIn("indices", primitive)
            self.assertIn("COLOR_0", primitive["attributes"])
            self.assertEqual(doc["meshes"][0]["extras"]["mayaUVSet"], "UVMap")
            self.assertEqual(doc["materials"][0]["extras"]["is_f3d"], 1)
            self.assertNotIn("glbImport", sys.modules)
            color_accessor = doc["accessors"][primitive["attributes"]["COLOR_0"]]
            view = doc["bufferViews"][color_accessor["bufferView"]]
            offset = 20 + json_length + 8 + view.get("byteOffset", 0)
            rgba = struct.unpack_from("<4f", payload, offset)
            for actual, expected in zip(rgba, (0.2, 0.4, 0.8, 0.5)):
                self.assertAlmostEqual(actual, expected, places=6)

    def test_skinned_glb_export_and_state_restoration(self):
        from fast64_maya import attributes
        from fast64_maya.vendor import animated_export
        mesh, _, _ = self.assigned()
        scene.convert_materials()
        paint.use_for_export()
        attributes.prepare()
        cmds.select(clear=True)
        root = cmds.joint(name="exportRoot", position=(0, 0, 0))
        child = cmds.joint(name="exportChild", position=(0, 1, 0))
        skin = cmds.skinCluster(root, child, mesh, toSelectedBones=True)[0]
        cmds.skinPercent(skin, mesh + ".vtx[*]", transformValue=[(root, .25), (child, .75)])
        cmds.setKeyframe(child, attribute="translateX", time=1, value=0)
        cmds.setKeyframe(child, attribute="translateX", time=2, value=2)
        cmds.currentTime(2)
        cmds.select(mesh)
        selection = cmds.ls(selection=True, long=True)
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "animated.glb")
            stats = animated_export.export_glb(path, start=1, end=2)
            self.assertEqual(stats["animationFrames"], 2)
            doc, binary = animated_export.read_static(path)
            self.assertEqual(len(doc["skins"]), 1)
            self.assertEqual(doc["animations"][0]["name"], "animation_00")
            primitive = doc["meshes"][0]["primitives"][0]
            weights = animated_export.accessor(doc, binary, primitive["attributes"]["WEIGHTS_0"])
            self.assertAlmostEqual(weights[0][0], .75)
            self.assertAlmostEqual(weights[0][1], .25)
            self.assertEqual(cmds.currentTime(query=True), 2)
            self.assertEqual(cmds.getAttr(skin + ".envelope"), 1)
            self.assertEqual(cmds.ls(selection=True, long=True), selection)
            cmds.setKeyframe(child, attribute="translateX", time=3, value=4)
            cmds.addAttr(mesh, longName="fast64AnimationClips", dataType="string")
            clips = [{"name":"idle", "start":1, "end":2}, {"name":"move", "start":2, "end":3}]
            cmds.setAttr(mesh + ".fast64AnimationClips", json.dumps(clips), type="string")
            multi = os.path.join(directory, "two_clips.glb")
            result = animated_export.export_glb(multi)
            self.assertEqual(result["clips"], ["idle", "move"])
            two, binary = animated_export.read_static(multi)
            self.assertEqual([clip["name"] for clip in two["animations"]], ["idle", "move"])
            import struct
            for clip in two["animations"]:
                times = two["accessors"][clip["samplers"][0]["input"]]
                view = two["bufferViews"][times["bufferView"]]
                first, last = struct.unpack_from("<2f", binary, view["byteOffset"])
                self.assertEqual(first, 0)
                self.assertAlmostEqual(last, 1 / 24, places=6)
            self.assertEqual(cmds.currentTime(query=True), 2)
            with self.assertRaises(ValueError):
                animated_export.validate_clips([clips[0], clips[0]])

    def test_command_and_plugin_lifecycle(self):
        self.assigned()
        raw = cmds.fast64(convert=True)
        result = json.loads(raw[0] if isinstance(raw, list) else raw)
        self.assertEqual(len(result["converted"]), 1)
        raw = cmds.fast64(validate=True, microcode="F3D")
        result = json.loads(raw[0] if isinstance(raw, list) else raw)
        self.assertEqual(result["triangles"], 12)
        with tempfile.TemporaryDirectory() as directory:
            raw = cmds.fast64(export=directory, name="command_cube")
            result = json.loads(raw[0] if isinstance(raw, list) else raw)
            self.assertTrue(os.path.isfile(os.path.join(result["directory"], "header.h")))
        cmds.unloadPlugin("fast64Maya.py")
        self.assertFalse(cmds.pluginInfo("fast64Maya.py", query=True, loaded=True))
        cmds.loadPlugin(os.path.join(HERE, "plug-ins", "fast64Maya.py"))


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(NativeTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    maya.standalone.uninitialize()
    sys.exit(0 if result.wasSuccessful() else 1)
