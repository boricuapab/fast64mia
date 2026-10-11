# Bundled GLB exporter

`glb_export.py` is the standalone native Maya static exporter developed with
this Maya integration, adapted from the companion Maya GLB importer/exporter
project. Only the export module is bundled; the importer and its plugin entrypoint
are not required or included. This copy uses the repository's GPL-3.0 license.

The module depends only on Maya, its bundled PySide2, and the Python standard
library. `export_glb(path, selected=True, f3d=True)` exports evaluated static
meshes with Fast64 material properties and relative external PNG references.
It does not export rigs, animation, visibility switches, or runtime code.
