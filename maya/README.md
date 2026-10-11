# Fast64 Maya — native port, version 0.1

This fork includes a **native Maya plugin**, independent of Blender. The
original Blender add-on remains at the repository root. Load the Maya entrypoint
below, not the Blender `__init__.py`.

This is an initial port of static asset workflows, **not feature parity with the
Blender add-on**. All code runs inside Maya; no Blender process or `bpy` is used.

## Install and load

1. Download or clone this repository and keep its files together.
2. Drag `maya/install.py` into Maya's viewport. The installer copies the plugin,
   scripts, and bundled GLB exporter into your user Maya modules directory.
3. Restart Maya, then open **Windows > Settings/Preferences > Plug-in Manager**.
4. Find **fast64Maya.py**, check **Loaded**, and check **Auto load** if desired.
5. Open **Fast64 > Fast64 Tools**.

No administrator access is needed. The installer uses Maya's user application
folder (including `MAYA_APP_DIR` when configured). It creates
`modules/Fast64Maya.mod` and `modules/Fast64Maya/` there. The module file uses a
relative path, so no checkout path or user-specific path is embedded in it.
Run the installer again to update an installation created by this installer.
Unload Fast64 and restart Maya after updating to use the new code.

Alternatively run `mayapy maya/install.py` from the checkout root. Use
`--maya-app-dir <folder>` to choose a custom Maya user application folder.

For development, Plug-in Manager's **Browse** button can load
`maya/plug-ins/fast64Maya.py` directly. Keep the entire `maya` directory together.
`maya/load_in_maya.py` also supports loading directly from a checkout.

## Convert Maya materials

1. Select polygon meshes or their parent transforms.
2. Click **Convert Maya Materials to F3D**.
3. Edit the new Lambert shader's **Extra Attributes** in the Attribute Editor.

Lambert, Blinn, Phong, Phong E, surfaceShader, Standard Surface, and Arnold
Standard Surface convert to tagged native F3D preview materials. The original
shaders remain available. Per-face assignments are preserved; changes are a
single undo step. Conversion preflights unsupported networks before changing
the scene and retains the mesh selection.

Diffuse/base color and diffuse weight become the N64 light color. File textures
connected directly to diffuse color are preserved. Repeat/offset UV settings,
wrap/clamp flags, and uniform opacity are supported. Image alpha previews in
Maya and exports as RGBA5551 (one-bit texture alpha). Blinn/Phong specular,
reflection, roughness, bump, and emission do not map to this port's N64 shading;
conversion reports the diffuse approximation. Procedural color networks,
separate opacity networks, UDIM, animated images, and complex placement settings
must be baked first and are rejected.

F3D attributes:

| Attribute | Meaning |
| --- | --- |
| `fast64Mode` | Shaded, TextureShaded, VertexColor, TextureVertexColor, Primitive |
| `fast64Layer` | Opaque, Transparent, Cutout |
| `fast64CullBack` | Back-face culling |
| `fast64Texture` | Image path for texture export |
| `fast64RepeatU/V`, `fast64OffsetU/V` | Export UV placement |
| `fast64WrapU/V` | Wrap versus clamp |
| `fast64Tint` | JSON RGB tint on converted textured materials |
| `fast64Opacity` | Constant opacity on converted textured materials |
| `fast64CollisionSurface` | Supported SM64 static surface macro |

Shaded modes export split normals and a single directional light plus ambient
light. Vertex-color modes use unlit color (RGBA) in place of normals. Primitive
mode uses a constant material color. Maya's Lambert/vertex-color preview is an
approximation, not an emulation of the N64 RDP.

## Vertex color paint

Select meshes, then use the panel's **Vertex Color Paint** section:

- **Show Colors / Show Alpha / Hide Colors** control the viewport display.
- Choose RGB, alpha, brush radius, opacity, Replace/Add/Scale/Smooth, and
  RGBA/RGB/Alpha channels; click **Paint Vertex Colors** and brush in the viewport.
- **Fill Selected Meshes / Vertices / Faces** fills only the selected components.
- **Use Painted Vertex Colors in F3D Export** switches assigned F3D materials
  to VertexColor or TextureVertexColor. This affects every mesh sharing that
  material, as any material edit does.
- **Prepare Col + Alpha Color Attributes and UVMap** adds named mesh color
  sets, copies existing painting into Col if needed, derives grayscale Alpha
  from Col opacity, and renames map1 to UVMap without changing UV coordinates.
  All changes are undoable. A conflicting existing UVMap is rejected. Running
  this button while Alpha is active preserves independent Alpha edits; otherwise
  it refreshes Alpha from Col's current opacity. The current color set becomes Col.

**Verify Actual Face Material Assignments** reports the F3D shaders that own
each face. Original Blinn/Phong nodes remain in the scene for reuse/undo; their
presence alone does not mean that they still shade the converted geometry.

## F3D GLB export

Use **Export Selected F3D GLB...** to prepare named color sets and UVMap,
then export through the bundled native Maya exporter. No separate GLB importer
installation is required. The output includes standard glTF materials, Fast64
material extras/extensions, painted RGBA, UVs, split normals, and per-face
materials. PNG companions use paths relative to the GLB; distribute them together.
Choose a new output filename; differing existing outputs are protected.

Unskinned meshes export the current evaluated pose as static geometry. Selecting
one linearly skinned mesh exports its bind geometry, up to four skin influences
per vertex, joint hierarchy, and the playback range as `animation_00`. Animation
is sampled at each scene frame. The mesh must use Y-up coordinates; animated
shear, dual-quaternion skinning, animated skin envelopes, and coincident vertices
with different weights are rejected. Maya time, selection, and skin envelope
are restored after export. This does not export animated visibility or
texture-switch scripts. Consumers that support only one joint per vertex use
the strongest influence; full glTF skin weights are retained in the file.

### Multiple animations on one rig

Author clips in separate timeline ranges on the same skeleton, such as idle
on frames 1–24 and walk on frames 25–48. Key all animated joints at each clip's
start and end to make its pose independent of the surrounding timeline.
Select the character mesh, open **Animation Clips...** in Fast64 Tools, and save:

```text
idle, 1, 24
walk, 25, 48
```

The named ranges are saved in the mesh transform's `fast64AnimationClips`
attribute with the Maya scene. **Export Selected F3D GLB...** exports each range
as a separate glTF animation starting at time zero. Without named ranges, the
playback range exports as `animation_00`. Exporting doesn't create new motion;
you must animate the additional range first.

For colored painting, first use **Use Painted Vertex Colors in F3D Export**.
These modes preserve painted RGB directly instead of multiplying it by the
old Lambert diffuse color. RGB comes from Col and alpha from Alpha when both
named sets exist. Imported texture shaders remain TextureVertexColor.

The native Artisan brush supports tablet pressure and standard tool settings.
Face-vertex painting preserves seams. The tool uses the current color set, or
creates an RGBA `Col` set initialized to white if there are no color sets. RGB
and alpha are read from that same current set at export. Painting alone does
not switch an existing lit material to vertex-color rendering; use the export
mode button. Color edits and fills support Maya Undo.

## Export

Select meshes, choose an output directory, asset name, microcode, and scale.
**Validate** checks the same scene data as export without writing output.

- F3D (16-vertex cache) and F3DEX2 (32-vertex cache) static GBI C display lists.
- Automatic triangulation of evaluated meshes without editing the scene.
- World-space geometry, split normals, UV seams, per-face materials, and RGBA
  color seams. Deformed meshes export their current evaluated pose as static
  geometry. Mirrored transforms correct triangle winding.
- Maya API positions are centimeters; the scale is N64 units per centimeter
  irrespective of the scene's display units. Y-up exports directly; Z-up maps
  `(x,y,z)` to `(x,z,-y)` for Y-up output.
- RGBA16 image textures are embedded in aligned `u64` C arrays. Width must be
  power-of-two 4..256, height power-of-two 1..256, and total size <=4096 bytes
  (for example 32x32 or 64x32). Resize oversized images before export.
- SM64 static collision exports seven surfaces: DEFAULT, BURNING, SLIPPERY,
  VERY_SLIPPERY, NOT_SLIPPERY, DEATH_PLANE, HANGABLE. Set the corresponding
  `SURFACE_...` material attribute. Collision export does not require conversion.
- Coordinates/UVs must fit signed 16-bit fields. Quantized degenerate triangles
  are rejected rather than exported with invalid geometry.

Output is `directory/asset_name/model.inc.c` + `header.h`, or
`collision.inc.c` + `collision.h`. Existing files are protected. Include the
generated C after your project's GBI / collision headers. Exported symbols are
`asset_name_dl` and `asset_name_collision`. Your project supplies these headers,
build integration, calls to the display list, and transparent draw ordering.
The list establishes its own rendering state; the caller should establish the
required state again before drawing other lists.

## Python API

```python
from fast64_maya import scene, paint
import os
output_directory = os.path.join(os.path.expanduser("~"), "fast64_exports")
scene.convert_materials()
paint.fill(rgb=(1, 0, 0), alpha=1)
paint.use_for_export()
scene.export(output_directory, name='my_model', scale=1, microcode='F3DEX2')
scene.export(output_directory, name='my_model', scale=1, collision_only=True)
```

The plugin also registers `cmds.fast64(convert=True)`, `validate=True`,
`paint=True`, and `export=output_directory` with `name`, `scale`, `microcode`, and
`collision` flags. Command results are returned as a single-element list
containing JSON, following Maya's Python command result conversion.

## Current limitations

SM64 geolayouts, level objects, SM64 animation/geolayout export, ROM/binary import/export, Ocarina
of Time scenes/rooms/actors/collision, Mario Kart tools, F3DEX3, custom combiners,
CI/IA/I textures, multi-texture materials, large-texture tiling, texture scroll,
and other Blender Fast64 tools are **not ported**. This initial native plugin
supports the material conversion, vertex paint, and static exports described
above. Do not treat it as a drop-in replacement for all Blender Fast64 features.

## Verification

Run isolated integration tests (does not touch the open Maya scene):

```text
mayapy maya/tests/test_native.py
mayapy maya/tests/test_installer.py
```

Run from the checkout root using Maya's `mayapy` executable. Tests use a
separate disposable standalone scene. `maya/tests/smoke_interactive.py` is an
optional interactive check; its report goes to the system temporary directory.

Checks native material conversion and undo, face assignments/color seams,
image encoding/UVs, cache batching and valid triangle indices, world coordinates,
mirroring/Z-up, collision export, invalid data, overwrite protection, commands,
and plugin lifecycle. An interactive smoke test additionally checks the panel
and native paint context. Actual in-game rendering is not yet verified.
