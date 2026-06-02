# DXF2CAD

A pure-Python tool for converting **3D DXF files** to industry-standard CAD formats (**STL**, **STEP**, **IGES**) — with an optional **STEP assembly export** that splits the model into individually selectable sub-parts in Creo, NX, and SolidWorks.

Available as a **[web app on GitHub Pages](https://hbradroc.github.io/DXF2CAD/)** (upload in browser, no install needed) and as a **command-line tool**.

---

## Supported Input Geometry

| Entity Type | Description | Handling |
|---|---|---|
| **3DFACE** | Triangle / quad faces _(most common)_ | Quads preserved as 4-sided faces |
| **MESH** | DXF 2010+ subdivision meshes | Tessellated to triangles |
| **POLYLINE** (PFACE) | Polyface mesh _(closed edge loops)_ | Extracted and tessellated |
| **POLYLINE** (POLYMESH) | Parametric M×N surface grids | Converted to regular triangle mesh; cylinders get fan-triangulated end caps |
| **INSERT** | Block references | Recursively expanded |

### Unsupported Entities
- **3DSOLID, BODY, REGION** — ACIS/SAT binary blobs. Export directly to STEP/STL from your source CAD application.
- **2D geometry** (LINE, CIRCLE, SPLINE) — Ignored silently.

---

## Output Formats

### STL — Stereolithography (`.stl`)
- Binary triangle mesh — compact, no topology
- Best for 3D printing, mesh viewers, FEA solvers

### STEP — ISO 10303-21 (`.stp`)
- AP214 `AUTOMOTIVE_DESIGN`, `ADVANCED_FACE` topology
- Best for Creo, SolidWorks, FreeCAD, NX
- **Flat part** (one shell) — or use `--assembly` for structured import

### STEP Assembly (`_assembly.stp`)
- Same AP214 format but with a full `PRODUCT` hierarchy
- Each spatially connected group of faces → one sub-part
- Imports into Creo / NX / SolidWorks as an **assembly tree** (each part independently selectable)

### IGES — Initial Graphics Exchange Specification (`.igs`)
- Version 5.3, Entity 144 (Trimmed Parametric Surfaces)
- Best for NX and legacy CAD tools

---

## How It Works

### Conversion Pipeline

```
Input DXF
    ↓
[1] Parse with ezdxf
    • Expand INSERT blocks recursively
    • Extract 3DFACE, MESH, POLYLINE entities
    ↓
[2] Geometry Extraction
    • 3DFACE → preserve quads or split to triangles
    • MESH / POLYLINE → tessellate; POLYMESH cylinders get end caps
    ↓
[3] T-Junction Stitching  (Patch #3)
    • Multi-tolerance vertex merge: 1e-4 → 1e-5 → 1e-6 mm
    • Closes floating-point seams without over-merging
    • Drops zero-area degenerate faces
    ↓
[4] Format Export
    • STL:           binary triangle mesh
    • STEP (part):   AP214 single OPEN_SHELL
    • STEP (assy):   AP214 multi-PRODUCT hierarchy  ← new
    • IGES:          Entity 144 trimmed parametric surfaces
    ↓
Output file(s)
```

### Assembly Export — How Parts Are Identified

The assembly uses **connected-component analysis** on the stitched mesh:

- Two faces are in the same component if they share at least one vertex after stitching.
- Union-Find propagates transitively — A touches B touches C → all one part.
- Each component → one `PRODUCT` sub-part in the STEP file.
- The stitch tolerance controls how aggressively gaps are closed before splitting:

| Stitch tolerance | Approx. components (Geniox example) |
|---|---|
| Tight (1e-6 mm) | More components (small gaps survive) |
| Default (1e-4–1e-6 mm) | ~275 components |
| Loose (5 mm) | ~245 components |

Use `--min-component-faces` to discard tiny mesh fragments.

### Quad Preservation

| Format | Quads | Result |
|---|---|---|
| STL | Split to 2 triangles (STL limitation) | More triangles |
| STEP | Preserved as 4-sided `ADVANCED_FACE` | Cleaner topology in Creo |
| IGES | Preserved as 4-sided trimmed surfaces | Cleaner topology |

---

## Web App (GitHub Pages)

Open **[hbradroc.github.io/DXF2CAD](https://hbradroc.github.io/DXF2CAD/)** — upload a DXF, pick options, download converted files. No install required. Runs entirely in your browser via [Pyodide](https://pyodide.org/).

| File | Role |
|------|------|
| `index.html` | Upload UI |
| `app.js` | Loads Pyodide, runs `dxf_converter.py` in the browser |
| `styles.css` | Layout |
| `dxf_converter.py` | Same converter as the CLI |

**Note:** First visit loads the Python runtime (~30s). Conversion runs locally — your DXF is never uploaded to a server.

### Enable GitHub Pages (one time)
1. Push repo to GitHub.
2. **Settings → Pages → Source: Deploy from branch → main / (root) → Save**.
3. Open `https://hbradroc.github.io/DXF2CAD/`.

### Test locally
```bash
python -m http.server 8080
# open http://localhost:8080
```

---

## Installation & Setup (CLI)

### Prerequisites
- Python 3.8 or later

### Install packages
```bash
pip install -r requirements.txt
```

Or minimal install: `pip install ezdxf numpy-stl numpy`

---

## Usage

### Command Syntax
```bash
python dxf_converter.py <input.dxf> [options]
```

### All Arguments

| Argument | Default | Description |
|---|---|---|
| `input` | required | Path to input `.dxf` file |
| `--format` | `iges` | Output format: `stl`, `step`, `iges`, or `all` |
| `--output` | auto | Output base path (no extension) |
| `--assembly` | off | Also write a `_assembly.stp` with per-component PRODUCT hierarchy |
| `--min-component-faces N` | `1` | Drop assembly components with fewer than N faces |
| `--stitch-tolerance T [T ...]` | `1e-4 1e-5 1e-6` | Vertex-merge distances coarse→fine (mm) |
| `--aggressive` | off | Also remove duplicate faces after stitching |
| `--normal-tolerance` | `1e-10` | Min normal length to accept a face |
| `--keep-degenerate` | off | Keep near-degenerate faces (may help with cylinder gaps) |

### Examples

#### Convert to IGES (default)
```bash
python dxf_converter.py model.dxf
# Output: model.igs
```

#### Convert to STEP (flat part)
```bash
python dxf_converter.py model.dxf --format step
# Output: model.stp
```

#### Convert to STEP + assembly (imports as assembly in Creo/NX)
```bash
python dxf_converter.py model.dxf --format step --assembly
# Output: model.stp  (flat part)
#         model_assembly.stp  (assembly — use this one in Creo)
```

#### Assembly with small fragment filtering
```bash
python dxf_converter.py model.dxf --format step --assembly --min-component-faces 4
# Drops any component with fewer than 4 faces (likely mesh artifacts)
```

#### All formats + assembly
```bash
python dxf_converter.py model.dxf --format all --assembly
# Output: model.igs, model.stp, model.stl, model_assembly.stp
```

#### Looser stitching (helps if parts split unexpectedly)
```bash
python dxf_converter.py model.dxf --format step --assembly --stitch-tolerance 5e-3 1e-4 1e-6
```

#### Tighter stitching (helps if separate parts merge together)
```bash
python dxf_converter.py model.dxf --format step --assembly --stitch-tolerance 1e-6
```

---

## Troubleshooting

### "No convertible geometry found in the DXF"
DXF contains only 2D or ACIS entities. Verify the file has 3DFACE, MESH, or POLYLINE entities. Export 3D surfaces explicitly from your source CAD tool.

### "ImportError: No module named ezdxf"
```bash
pip install ezdxf numpy-stl numpy
```

### STEP/IGES file is very large
Expected — STEP/IGES store full topology (edge loops, normals, surface definitions) per face. A 50K-triangle model produces ~40MB STEP vs ~2MB STL.

### Assembly has too many parts / parts are fragmented
The mesh has small gaps that prevent faces from stitching together. Try a looser stitch tolerance:
```bash
--stitch-tolerance 1e-3 1e-4 1e-6
```

### Assembly merges parts that should be separate
The stitch tolerance is too loose. Tighten it:
```bash
--stitch-tolerance 1e-6
```

### Web app blocked on corporate network
The page tries two CDN sources (`cdn.pyodide.org` then `cdn.jsdelivr.net`). If both are blocked, use a VPN or run the CLI locally instead.

### Creo import broken when importing IGES as assembly
The IGES format has no assembly structure — use the `_assembly.stp` file and import as STEP assembly instead.

---

## Technical Details

### STEP Part Implementation
- Standard: ISO 10303-21, AP214 (Automotive Design)
- Each face → `ADVANCED_FACE` on `PLANE` in a single `OPEN_SHELL`
- Precision: 1E-07 mm uncertainty measure
- **Units:** Millimetres (`SI_UNIT(.MILLI.,.METRE.)`) — matches typical DXF `$INSUNITS=4`

### STEP Assembly Implementation
- Same AP214 standard
- Each connected component → `PRODUCT` + `PRODUCT_DEFINITION` + own `OPEN_SHELL`
- Top-level `PRODUCT` links all components via `NEXT_ASSEMBLY_USAGE_OCCURRENCE`
- Pure Python — no CAD kernel required

### T-Junction Stitching
- Grid-hash vertex merge at multiple tolerances (coarse → fine)
- Triangles and quads always merged together (shared vertex pool)
- Degenerate faces (collapsed edges after merge) dropped automatically

### IGES Implementation
- Version 5.3
- Entity 144 (Trimmed Parametric Surface) with Entity 102 (Composite Curve) boundaries
- Entity 108 (Plane) surfaces

### STL Implementation
- Binary format (compact, no ASCII overhead)
- Normals computed per-triangle from vertex cross-product
- Falls back to pure-Python writer if `numpy-stl` unavailable (browser/Pyodide)

---

## Limitations

| Issue | Workaround |
|---|---|
| DXF ACIS solids not supported | Export directly to STEP/STL from source CAD tool |
| STL requires triangle tessellation | Use STEP/IGES for quad preservation |
| Assembly part names are sequential (Part_001…) | Rename in Creo after import |
| Very large files (1M+ faces) may be slow in browser | Use CLI for large models |
| Assembly split is geometry-only — no semantic part names | DXF carries no component naming information |

---

## License

**MIT License** — Free for commercial and personal use.

---

## Version History

| Version | Changes |
|---|---|
| **1.4** (Current) | **STEP assembly export** — connected-component decomposition writes proper AP214 assembly hierarchy; `--assembly` and `--min-component-faces` flags; web UI "Export as assembly" checkbox |
| 1.3 | **T-junction stitching** — multi-tolerance vertex merge (1e-4→1e-6 mm); `--stitch-tolerance` flag; fixed IndexError on mixed tri/quad meshes; CDN fallback for blocked corporate networks |
| 1.2 | Cylinder end cap generation for POLYMESH; `--normal-tolerance` and `--keep-degenerate` flags |
| 1.1 | Quad preservation in STEP/IGES; improved face topology |
| 1.0 | Initial release; STEP/IGES/STL export |
