# DXF2CAD

A lightweight, pure-Python command-line tool for converting **3D DXF files** to industry-standard CAD formats (**STL**, **STEP**, **IGES**). Ideal for CAD workflows, 3D printing preparation, and multi-tool design ecosystems.

## Supported Input Geometry

| Entity Type | Description | Handling |
|---|---|---|
| **3DFACE** | Triangle / quad faces _(most common)_ | Quads preserved as 4-sided faces; degenerate quads → triangles |
| **MESH** | DXF 2010+ subdivision meshes | Tessellated to triangles |
| **POLYLINE** (PFACE) | Polyface mesh _(closed edge loops)_ | Extracted and tessellated |
| **POLYLINE** (POLYMESH) | Parametric M×N surface grids | Converted to regular triangle mesh |
| **INSERT** | Block references | Recursively expanded to extract nested geometry |

### Unsupported Entities
- **3DSOLID, BODY, REGION** — These store geometry as embedded ACIS/SAT binary blobs. To include them, export directly to STEP/STL from your source CAD application.
- **2D geometry** (LINE, CIRCLE, SPLINE) — Ignored silently

---

## Output Formats

### STL — Stereolithography (`.stl`)
- **Format:** Binary triangle mesh
- **Best for:** 3D printing, mesh visualization, FEA solvers
- **Attributes:** Pure geometry; no topology or features
- **File size:** Compact (typically smallest of the three formats)

### STEP — ISO 10303-21 (`.stp`)
- **Standard:** AP214 (`AUTOMOTIVE_DESIGN`)
- **Structure:** `SHELL_BASED_SURFACE_MODEL` containing `ADVANCED_FACE` entities
- **Best for:** Professional CAD workflows, Creo, SolidWorks, FreeCAD, NX
- **Geometry precision:** 1E-07 millimeters (per ISO 10303)

### IGES — Initial Graphics Exchange Specification (`.igs`)
- **Version:** 5.3
- **Geometry:** Entity 144 (Trimmed Parametric Surfaces) with Entity 102 (Composite Curves)


---

## How It Works

### Conversion Pipeline

```
Input DXF
    ↓
[1] Parse modelspace with ezdxf
    • Expand INSERT blocks recursively
    • Extract 3DFACE, MESH, POLYLINE entities
    • Collect vertices and face indices
    ↓
[2] Geometry Extraction
    • 3DFACE → preserve quads, detect degenerate faces
    • MESH → tessellate to triangles
    • POLYLINE → expand Polyface/Polymesh
    ↓
[3] Mesh Cleaning (optional)
    • Verify vertex normals
    • Skip zero-area faces
    • Merge duplicate vertices (if --aggressive flag used)
    ↓
[4] Format-Specific Export
    • STL:  Binary triangle mesh
    • STEP: AP214 shell with ADVANCED_FACE topology
    • IGES: Entity 144 trimmed parametric surfaces
    ↓
Output CAD-ready file(s)
```

### Quad Preservation (No Diagonal Splits)

When a DXF file contains **quad faces** (4-sided polygons), they are preserved as **4-sided ADVANCED_FACE entities** in STEP and IGES output:

| Format | Quads | Result |
|---|---|---|
| STL | Split to 2 triangles (STL limitation) | 15,460 triangles from 9,704 tri + 2,878 quad |
| STEP | Preserved as 4-sided faces | 11,938 faces (no unnecessary diagonals) |
| IGES | Preserved as 4-sided surfaces | 11,938 trimmed surfaces (clean topology) |

**Example:** A rectangular roof opening (quad) exports as a single selectable 4-sided face in Creo instead of 2 triangles with a diagonal split.

---

## Installation & Setup

### Prerequisites

- **Python 3.8 or later** — [Download here](https://www.python.org/downloads/)
- **Windows, macOS, or Linux** — Platform-independent

### 1. Install Python Packages

```bash
pip install ezdxf numpy-stl numpy
```

**What each package does:**
- `ezdxf` — Parses DXF files and extracts 3D geometry
- `numpy-stl` — Writes binary STL files efficiently
- `numpy` — Numerical operations (normal vectors, mesh math)

**STEP and IGES export require no additional packages** — writers are implemented in pure Python.

### 2. Download or clone the converter

```bash
git clone <repository-url>
cd dxf_converter
```

Or place `dxf_converter.py` in your project directory.

---

## Usage

### Command Syntax

```bash
python dxf_converter.py <input.dxf> [--format FORMAT] [--output PATH] [--aggressive]
```

### Arguments

| Argument | Type | Default | Description |
|---|---|---|---|
| `input` | Path | **required** | Path to input `.dxf` file |
| `--format` | str | `iges` | Output format(s): `stl`, `step`, `iges`, or `all` |
| `--output` | Path | *auto* | Output base path (no extension). If omitted, uses input filename |
| `--aggressive` | flag | off | Apply aggressive mesh optimization (slower, better for Creo) |

### Examples

#### Convert to IGES (default — recommended for NX)
```bash
python dxf_converter.py my_roof.dxf
# Output: my_roof.igs (24.2 MB)
```

#### Convert to STEP (Creo, SolidWorks, FreeCAD)
```bash
python dxf_converter.py my_roof.dxf --format step
# Output: my_roof.stp (16.8 MB)
```

#### Convert to STL (3D printing, mesh viewers)
```bash
python dxf_converter.py my_roof.dxf --format stl
# Output: my_roof.stl (773 KB)
```

#### Export all three formats at once
```bash
python dxf_converter.py my_roof.dxf --format all
# Output: my_roof.igs, my_roof.stp, my_roof.stl
```

#### Custom output directory
```bash
python dxf_converter.py my_roof.dxf --format all --output /exports/roof_v2
# Output: /exports/roof_v2.igs, /exports/roof_v2.stp, /exports/roof_v2.stl
```

#### Aggressive mesh optimization (for Creo imports)
```bash
python dxf_converter.py my_roof.dxf --format step --aggressive
# Simplifies mesh + merges vertices before export
```

---

## Troubleshooting

### ❌ "No convertible geometry found in the DXF"
**Cause:** DXF contains only 2D entities (LINE, CIRCLE, SPLINE) or ACIS solids.

**Solution:**
- Verify the source DXF contains 3D mesh entities (3DFACE, MESH, POLYLINE)
- In your source CAD tool, export 3D surfaces/faces explicitly (not edge wireframes)
- For ACIS solids, export directly to STEP/STL from your CAD program

### ❌ ImportError: "No module named 'ezdxf'"
**Cause:** Required package not installed.

**Solution:**
```bash
pip install ezdxf numpy-stl numpy
```

### ⚠️ "Large file size" (STEP/IGES >> STL)
**Expected behavior:** STEP and IGES store one entity per face + topology metadata.

- 50,000 triangles → ~40+ MB STEP file (normal)
- 50,000 triangles → ~1-2 MB STL file (compact mesh-only format)

**Why the difference?** STEP/IGES include edge loops, normal vectors, surface definitions, and product metadata for CAD feature work. STL is pure geometry.

### ⚠️ Warnings about degenerate faces
**Meaning:** Zero-area polygons (duplicate vertices, collinear points) detected and skipped.

**Resolution:** Automatic. These faces contribute nothing to the mesh and are safely filtered.

### ❌ CAD import shows "broken surfaces" or "topology errors"
**Possible cause:** Inverted face normals or non-manifold geometry in source DXF.

**Solution:**
1. Try importing as a reference model (visualization only)
2. Use the `--aggressive` flag to clean the mesh:
   ```bash
   python dxf_converter.py model.dxf --format step --aggressive
   ```
3. In Creo: Create a new part → Insert DXF as reference → Re-surface

### ❌ Quads are splitting into triangles in STEP
**Cause:** Using older version of the converter that splits all quads.

**Solution:** Ensure `dxf_converter.py` is up to date. The current version preserves quads as 4-sided faces in STEP/IGES output.

---

## Advanced Options

### Mesh Optimization (`--aggressive`)

When enabled, the converter applies vertex merging for triangle-only meshes:

```bash
python dxf_converter.py model.dxf --format step --aggressive
```

**Effects:**
- Merges vertices within tolerance (1e-6 mm)
- Removes duplicate faces
- Reduces file size slightly
- ⚠️ **Note:** Only applied when no quads are present (quads are always preserved)

### Custom Output Path

Useful for organizing exports into subdirectories:

```bash
python dxf_converter.py roof_concept.dxf --format all --output ./exports/roof_v2
# Creates: ./exports/roof_v2.igs, ./exports/roof_v2.stp, ./exports/roof_v2.stl
```

---

## Performance Metrics

| Operation | Time | Notes |
|---|---|---|
| Parse 22K-vertex DXF | ~500 ms | Via ezdxf |
| Extract geometry | ~100 ms | Tessellation + indexing |
| Write STL | ~50 ms | Binary format, compact |
| Write STEP | ~2 s | Complex topology encoding |
| Write IGES | ~3 s | Verbose entity structure |
| **Total (all formats)** | ~6 s | For typical roof concept model |

**Scalability:** Tested up to 100K+ vertices / 50K+ faces on standard hardware.

---

## Technical Details

### STEP Implementation
- **Standard:** ISO 10303-21, Part 214 (Automotive Design)
- **Topology:** Each face → `ADVANCED_FACE` on `PLANE` surface in `OPEN_SHELL`
- **Precision:** 1E-07 mm uncertainty measure
- **Units:** Meters (SI)

### IGES Implementation
- **Version:** 5.3
- **Geometry:** Entity 144 (Trimmed Parametric Surface)
- **Boundaries:** Entity 102 (Composite Curve) + Entity 110 (Line) edges
- **Surface:** Entity 108 (Plane)

### STL Implementation
- **Format:** Binary (compact, no ASCII overhead)
- **Tessellation:** All faces converted to triangles (STL limitation)
- **Normals:** Computed per-triangle from vertex cross-product

---

## Limitations & Known Issues

| Issue | Workaround |
|---|---|
| DXF ACIS solids not supported | Export directly to STEP/STL from source CAD tool |
| STL format requires triangle tessellation | Use STEP/IGES for quad preservation |
| Very large files (1M+ faces) may be slow | Split DXF into multiple files before conversion |
| Some legacy IGES viewers struggle with complex topology | Use STEP format instead; better supported |

---

## License

**MIT License** — Free for commercial and personal use.

---

## Version History

| Version | Changes |
|---|---|
| **1.1** (Current) | Quad preservation in STEP/IGES; improved face topology |
| 1.0 | Initial release; STEP/IGES/STL export |

---


---

**Questions?** See [Troubleshooting](#troubleshooting) or review the [Usage](#usage) section.
