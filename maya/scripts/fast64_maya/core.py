"""Host-independent static N64 display-list and SM64 collision writers.

Outputs GBI macro C, for inclusion in a project supplying ultra64.h/gbi.h.
Geometry is already transformed to the target coordinates by the host adapter.
"""
import math
import re
from collections import OrderedDict


def identifier(name):
    value = re.sub(r"[^a-zA-Z0-9_]", "_", name)
    if not value or value[0].isdigit():
        value = "f64_" + value
    return value


def integer(value, low, high, label):
    if not math.isfinite(value):
        raise ValueError("{} is not finite".format(label))
    result = int(round(value))
    if result < low or result > high:
        raise ValueError("{} ({}) is outside {}..{}; reduce export scale".format(label, result, low, high))
    return result


def rgba8(color):
    if len(color) != 4 or any(not math.isfinite(c) for c in color):
        raise ValueError("Invalid RGBA color")
    return tuple(int(round(max(0.0, min(1.0, c)) * 255)) for c in color)


def rgba16(width, height, pixels):
    """RGBA5551 texture words, ordered from the top-left image pixel."""
    if width < 4:
        raise ValueError("RGBA16 texture width must be at least 4 pixels for GBI block loading")
    for size in (width, height):
        if size < 1 or size > 256 or size & (size - 1):
            raise ValueError("RGBA16 textures must have power-of-two dimensions (1..256)")
    if width * height * 2 > 4096:
        raise ValueError("RGBA16 texture exceeds N64's 4096-byte TMEM; resize it")
    if len(pixels) != width * height:
        raise ValueError("Texture pixel count does not match dimensions")
    words = []
    for pixel in pixels:
        r, g, b, a = rgba8(pixel)
        words.append((int(round(r * 31 / 255)) << 11) | (int(round(g * 31 / 255)) << 6)
                     | (int(round(b * 31 / 255)) << 1) | (1 if a >= 128 else 0))
    return words


def vertex(corner, material):
    xyz = tuple(integer(v, -32768, 32767, "Vertex coordinate") for v in corner["position"])
    if len(xyz) != 3:
        raise ValueError("Vertex needs three coordinates")
    uv = (0, 0)
    if material["mode"] in ("TextureVertexColor", "TextureShaded"):
        width, height = material["texture"]["width"], material["texture"]["height"]
        u, v = corner["uv"]
        uv = (integer(u * width * 32, -32768, 32767, "S coordinate"),
              integer((1 - v) * height * 32, -32768, 32767, "T coordinate"))
    color = rgba8(corner["color"])
    if material["mode"] in ("Shaded", "TextureShaded"):
        normal = corner["normal"]
        color = tuple(integer(n * 127, -127, 127, "Normal") & 255 for n in normal) + (color[3],)
    return xyz + uv + color


def batches(triangles, material, limit):
    vertices, lookup, faces = [], {}, []
    for triangle in triangles:
        if len(triangle) != 3:
            raise ValueError("Expected triangles")
        converted = [vertex(c, material) for c in triangle]
        needed = set(converted) - set(lookup)
        if faces and len(vertices) + len(needed) > limit:
            yield vertices, faces
            vertices, lookup, faces = [], {}, []
        indices = []
        for v in converted:
            if v not in lookup:
                lookup[v] = len(vertices)
                vertices.append(v)
            indices.append(lookup[v])
        if len(set(indices)) != 3:
            raise ValueError("Triangle collapses after integer quantization; increase export scale")
        a, b, c = [vertices[i][:3] for i in indices]
        ab, ac = [b[i] - a[i] for i in range(3)], [c[i] - a[i] for i in range(3)]
        cross = (ab[1]*ac[2] - ab[2]*ac[1], ab[2]*ac[0] - ab[0]*ac[2], ab[0]*ac[1] - ab[1]*ac[0])
        if cross == (0, 0, 0):
            raise ValueError("Triangle has zero area after integer quantization")
        faces.append(indices)
    if faces:
        yield vertices, faces


def display_list(name, groups, microcode="F3DEX2"):
    if microcode not in ("F3D", "F3DEX2"):
        raise ValueError("Supported microcodes: F3D, F3DEX2")
    name = identifier(name)
    limit = 16 if microcode == "F3D" else 32
    data = ["/* Native Fast64 Maya export. Include after your project's GBI headers. */"]
    commands = []
    total_vertices = total_triangles = 0
    for group_index, group in enumerate(groups):
        material = group["material"]
        mode = material["mode"]
        if mode not in ("VertexColor", "TextureVertexColor", "Primitive", "Shaded", "TextureShaded"):
            raise ValueError("Unsupported material mode: " + mode)
        prefix = "{}_mat_{}".format(name, group_index)
        commands.extend(["gsDPPipeSync()", "gsDPSetCycleType(G_CYC_1CYCLE)",
                         "gsDPSetAlphaCompare(G_AC_NONE)", "gsDPSetDepthSource(G_ZS_PIXEL)",
                         "gsSPClearGeometryMode(G_LIGHTING | G_TEXTURE_GEN | G_TEXTURE_GEN_LINEAR | G_FOG | G_CULL_FRONT | G_CULL_BACK)",
                         "gsSPSetGeometryMode(G_SHADE | G_SHADING_SMOOTH | G_ZBUFFER{} )".format(
                             " | G_CULL_BACK" if material.get("cull_back", True) else "")])
        layer = material.get("layer", "Opaque")
        if layer not in ("Opaque", "Transparent", "Cutout"):
            raise ValueError("Unsupported render layer")
        render = {"Opaque": "OPA_SURF", "Transparent": "XLU_SURF", "Cutout": "TEX_EDGE"}[layer]
        commands.append("gsDPSetRenderMode(G_RM_AA_ZB_{0}, G_RM_AA_ZB_{0}2)".format(render))
        combine = {"VertexColor": "G_CC_SHADE", "TextureVertexColor": "G_CC_MODULATERGBA", "Primitive": "G_CC_PRIMITIVE",
                   "Shaded": "G_CC_SHADE", "TextureShaded": "G_CC_MODULATERGBA"}[mode]
        commands.append("gsDPSetCombineMode({0}, {0})".format(combine))
        if mode in ("Shaded", "TextureShaded"):
            rgb = rgba8(material["color"])[:3]
            data.append("static Lights1 {}_lights = gdSPDefLights1({}, {}, {}, {}, {}, {}, 73, 73, 73);".format(
                prefix, *(tuple(int(c/3) for c in rgb) + tuple(int(c*2/3) for c in rgb))))
            commands.extend(["gsSPSetGeometryMode(G_LIGHTING)", "gsSPSetLights1({}_lights)".format(prefix)])
        if mode == "Primitive":
            commands.append("gsDPSetPrimColor(0, 0, {}, {}, {}, {})".format(*rgba8(material["color"])))
        if mode in ("TextureVertexColor", "TextureShaded"):
            texture = material["texture"]
            width, height = texture["width"], texture["height"]
            words = rgba16(width, height, texture["pixels"])
            # u64 ensures RSP/RDP texture data alignment without compiler-specific attributes.
            packed = [sum(words[i+j] << (48 - j*16) for j in range(min(4, len(words)-i)))
                      for i in range(0, len(words), 4)]
            data.append("static const u64 {}_texture[] = {{\n{}\n}};".format(
                prefix, "\n".join("    0x{:016X}ULL,".format(word) for word in packed)))
            commands.extend(["gsSPTexture(0xFFFF, 0xFFFF, 0, G_TX_RENDERTILE, G_ON)",
                             "gsDPSetTexturePersp(G_TP_PERSP)", "gsDPSetTextureFilter(G_TF_BILERP)",
                             "gsDPSetTextureLOD(G_TL_TILE)", "gsDPSetTextureLUT(G_TT_NONE)",
                             "gsDPSetTextureDetail(G_TD_CLAMP)", "gsDPSetTextureConvert(G_TC_FILT)",
                             "gsDPLoadTextureBlock({}_texture, G_IM_FMT_RGBA, G_IM_SIZ_16b, {}, {}, 0, G_TX_WRAP, G_TX_WRAP, {}, {}, G_TX_NOLOD, G_TX_NOLOD)".format(
                                 prefix, width, height, int(math.log2(width)), int(math.log2(height)))])
            if not material.get("wrap_s", True) or not material.get("wrap_t", True):
                commands[-1] = commands[-1].replace("G_TX_WRAP, G_TX_WRAP", "{}, {}".format(
                    "G_TX_WRAP" if material.get("wrap_s", True) else "G_TX_CLAMP",
                    "G_TX_WRAP" if material.get("wrap_t", True) else "G_TX_CLAMP"))
        else:
            commands.append("gsSPTexture(0, 0, 0, G_TX_RENDERTILE, G_OFF)")
        for batch_index, (verts, faces) in enumerate(batches(group["triangles"], material, limit)):
            symbol = "{}_vtx_{}".format(prefix, batch_index)
            data.append("static const Vtx {}[] = {{\n{}\n}};".format(symbol, "\n".join(
                "    {{{%d, %d, %d}, 0, {%d, %d}, {%d, %d, %d, %d}}}," % v for v in verts)))
            commands.append("gsSPVertex({}, {}, 0)".format(symbol, len(verts)))
            commands.extend("gsSP1Triangle({}, {}, {}, 0)".format(*face) for face in faces)
            total_vertices += len(verts)
            total_triangles += len(faces)
    if not total_triangles:
        raise ValueError("No triangles to export")
    commands.extend(["gsDPPipeSync()", "gsSPTexture(0, 0, 0, G_TX_RENDERTILE, G_OFF)", "gsSPEndDisplayList()"])
    data.append("const Gfx {}_dl[] = {{\n{}\n}};".format(name, "\n".join("    " + c + "," for c in commands)))
    header = "/* Include after your project's GBI headers. */\nextern const Gfx {}_dl[];\n".format(name)
    return "\n\n".join(data) + "\n", header, {"vertices": total_vertices, "triangles": total_triangles}


def collision(name, groups):
    """SM64 static collision only, surface type is explicitly attached to each group."""
    vertices, lookup, surfaces = [], {}, OrderedDict()
    for group in groups:
        surface = group.get("surface", "SURFACE_DEFAULT")
        if not re.fullmatch(r"SURFACE_[A-Z0-9_]+", surface):
            raise ValueError("Invalid SM64 surface identifier")
        for triangle in group["triangles"]:
            face = []
            for corner in triangle:
                p = tuple(integer(c, -32768, 32767, "Collision coordinate") for c in corner["position"])
                if p not in lookup:
                    lookup[p] = len(vertices)
                    vertices.append(p)
                face.append(lookup[p])
            if len(set(face)) != 3:
                raise ValueError("Collision triangle collapses after quantization")
            a, b, c = [vertices[i] for i in face]
            ab, ac = [b[i]-a[i] for i in range(3)], [c[i]-a[i] for i in range(3)]
            if (ab[1]*ac[2]-ab[2]*ac[1], ab[2]*ac[0]-ab[0]*ac[2], ab[0]*ac[1]-ab[1]*ac[0]) == (0, 0, 0):
                raise ValueError("Collision triangle has zero area after quantization")
            surfaces.setdefault(surface, []).append(face)
    if not vertices:
        raise ValueError("No collision triangles")
    if len(vertices) > 32767 or any(len(faces) > 32767 for faces in surfaces.values()):
        raise ValueError("Collision exceeds signed 16-bit vertex/triangle count")
    commands = ["COL_INIT()", "COL_VERTEX_INIT({})".format(len(vertices))]
    commands.extend("COL_VERTEX({}, {}, {})".format(*v) for v in vertices)
    for surface, faces in surfaces.items():
        commands.append("COL_TRI_INIT({}, {})".format(surface, len(faces)))
        commands.extend("COL_TRI({}, {}, {})".format(*f) for f in faces)
    commands.extend(["COL_TRI_STOP()", "COL_END()"])
    name = identifier(name)
    source = "const Collision {}_collision[] = {{\n{}\n}};\n".format(name, "\n".join("    " + c + "," for c in commands))
    return source, "extern const Collision {}_collision[];\n".format(name), {
        "vertices": len(vertices), "triangles": sum(len(f) for f in surfaces.values())}
