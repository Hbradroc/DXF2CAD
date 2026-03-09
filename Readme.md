# DXF2CAD

A pure-Python command-line tool that converts **3D DXF files** into industry-standard CAD formats — **STL**, **STEP**, and **IGES** 

---

## Supported Input Geometry

| DXF Entity | Description |
|---|---|
| `3DFACE` | Triangle / quad faces — most common mesh primitive |
| `MESH` | DXF 2010+ subdivision meshes |
| `POLYLINE` (PFACE) | Polyface meshes |
| `POLYLINE` (POLYMESH) | Parametric M×N surface grids |
| `INSERT` | Block references — recursively expanded |

> **Not supported:** `3DSOLID`, `BODY`, `REGION` — these contain embedded ACIS/SAT blobs that require a full CAD kernel to decode. Export those directly from your CAD application.

---

## Output Formats

### STL (`.stl`)
- Binary triangle mesh
- Best for **3D printing** and mesh viewers
- Widely supported (Meshmixer, Blender, PrusaSlicer, etc.)

### STEP (`.stp`)
- ISO 10303 AP214 (`AUTOMOTIVE_DESIGN`) — pure-Python writer, no extra library
- Each triangle becomes an `ADVANCED_FACE` on a `PLANE` surface inside an `OPEN_SHELL`
- Best for **NX, CATIA, SolidWorks, FreeCAD, Fusion 360**
- Import in NX: **File → Import → STEP**

### IGES (`.igs`)
- IGES 5.3 — pure-Python writer, no extra library
- Each triangle becomes an Entity 144 **Trimmed Parametric Surface**
- Widely supported by all major CAD packages
- Import in NX: **File → Import → IGES**

---

## How Conversion Works

```
DXF file
   │
   ├─ ezdxf parses all modelspace entities
   │     ├─ 3DFACE  → triangles / quads
   │     ├─ MESH    → indexed face list
   │     └─ POLYLINE (Polyface / Polymesh) → M×N grid faces
   │
   ├─ All faces are tessellated into triangles
   │     (quads split into 2 triangles, degenerate faces skipped)
   │
   └─ Writer encodes triangles into the chosen format
         ├─ STL   → binary triangle mesh
         ├─ STEP  → ADVANCED_FACE entities in AP214 shell
         └─ IGES  → Entity 144 trimmed surfaces
```

---

## Installation

### 1. Python 3.8+

Download from [python.org](https://www.python.org/downloads/) if not already installed.

### 2. Install dependencies

```bash
pip install ezdxf numpy-stl numpy
```

That's it — STEP and IGES export are pure Python with no additional packages.

---

## Usage

```bash
python dxf_converter.py <input.dxf> [--format FORMAT] [--output OUTPUT]
```

### Arguments

| Argument | Description |
|---|---|
| `input` | Path to the `.dxf` file to convert |
| `--format` | Output format: `iges` (default), `step`, `stl`, or `all` |
| `--output` | Output base path without extension (defaults to same folder/name as input) |

### Examples

```bash
# IGES (default) — best choice for NX import
python dxf_converter.py model.dxf

# STEP
python dxf_converter.py model.dxf --format step

# STL
python dxf_converter.py model.dxf --format stl

# All three formats at once
python dxf_converter.py model.dxf --format all

# Custom output path
python dxf_converter.py model.dxf --format all --output exports/my_model
```

Output files are saved next to the input file by default:
```
model.dxf   →   model.igs  /  model.stp  /  model.stl
```
---

## Troubleshooting

**"No convertible geometry found"**  
Your DXF contains only 2D entities or ACIS solids. Re-export from your CAD tool making sure 3D face/mesh output is enabled.

**Large file size**  
IGES and STEP store one entry per triangle face. A model with 50,000 triangles will produce a large file — this is normal.

**Warnings about degenerate triangles**  
Zero-area triangles (e.g. duplicate vertices) are automatically skipped.

---

## License

MIT
