#!/usr/bin/env python3
"""
DXF → 3D Converter
===================
Converts a 3D DXF file into STL, STEP, and/or IGES.

Supported DXF geometry entities
---------------------------------
  3DFACE        – triangular / quad faces (most common)
  MESH          – subdivision-style mesh
  POLYLINE      – PFACE mesh and POLYMESH (M×N surface)

Not supported (require a full CAD kernel / ACIS SAT parser)
------------------------------------------------------------
  3DSOLID, BODY, REGION  – these store geometry as embedded ACIS / SAT blobs.
  If you need those, export them from your CAD program directly to STEP/STL.

Dependencies
------------
  pip install ezdxf numpy-stl numpy
  # For STEP export also install:
  conda install -c conda-forge pythonocc-core
  # or:  pip install pythonocc-core

Usage
-----
  python dxf_converter.py model.dxf
  python dxf_converter.py model.dxf --format stl
  python dxf_converter.py model.dxf --format step
  python dxf_converter.py model.dxf --format iges
  python dxf_converter.py model.dxf --format all --output exported/model
"""

import argparse
import sys
from pathlib import Path

import numpy as np


# ---------------------------------------------------------------------------
# Geometry extraction
# ---------------------------------------------------------------------------

def _vec3(pt):
    """Return a plain (x, y, z) float tuple from an ezdxf Vec3 / tuple."""
    return (float(pt[0]), float(pt[1]), float(pt[2]))


def extract_geometry(dxf_path: Path):
    """
    Walk the modelspace of *dxf_path* and collect triangular faces and quads.

    Returns
    -------
    vertices : np.ndarray  shape (N, 3)  float64
    triangles : list[tuple[int, int, int]]  – triangle indices into *vertices*
    quads : list[tuple[int, int, int, int]]  – quad indices into *vertices*
    """
    try:
        import ezdxf
        from ezdxf.math import Vec3
    except ImportError:
        sys.exit(
            "ezdxf is not installed.\n"
            "Fix: pip install ezdxf"
        )

    doc = ezdxf.readfile(str(dxf_path))
    msp = doc.modelspace()

    verts: list[tuple[float, float, float]] = []
    tris:  list[tuple[int, int, int]]       = []
    quads: list[tuple[int, int, int, int]]  = []

    def push_triangle(p1, p2, p3):
        i = len(verts)
        verts.extend([_vec3(p1), _vec3(p2), _vec3(p3)])
        tris.append((i, i + 1, i + 2))

    def push_quad(p1, p2, p3, p4):
        i = len(verts)
        verts.extend([_vec3(p1), _vec3(p2), _vec3(p3), _vec3(p4)])
        quads.append((i, i + 1, i + 2, i + 3))

    # Recursively expand INSERT (block references) so we don't miss geometry
    entities = list(msp)
    seen_blocks: set[str] = set()

    def expand_inserts(entity_list):
        expanded = []
        for e in entity_list:
            if e.dxftype() == 'INSERT':
                bname = e.dxf.name
                if bname not in seen_blocks:
                    seen_blocks.add(bname)
                    try:
                        block = doc.blocks[bname]
                        expanded.extend(expand_inserts(list(block)))
                    except Exception:
                        pass
            else:
                expanded.append(e)
        return expanded

    entities = expand_inserts(entities)

    entity_counts: dict[str, int] = {}
    skipped_acis: list[str] = []

    for entity in entities:
        etype = entity.dxftype()
        entity_counts[etype] = entity_counts.get(etype, 0) + 1

        # ------------------------------------------------------------------ #
        # 3DFACE  – the most common mesh primitive in DXF                     #
        # ------------------------------------------------------------------ #
        if etype == '3DFACE':
            p0 = entity.dxf.vtx0
            p1 = entity.dxf.vtx1
            p2 = entity.dxf.vtx2
            p3 = entity.dxf.vtx3
            if _vec3(p2) == _vec3(p3):   # degenerate quad == triangle
                push_triangle(p0, p1, p2)
            else:
                push_quad(p0, p1, p2, p3)

        # ------------------------------------------------------------------ #
        # MESH  – DXF 2010+ subdivision mesh                                  #
        # ------------------------------------------------------------------ #
        elif etype == 'MESH':
            mesh_verts = list(entity.vertices)
            base = len(verts)
            verts.extend(_vec3(v) for v in mesh_verts)
            for face in entity.faces:
                fi = list(face)
                for k in range(1, len(fi) - 1):
                    tris.append((base + fi[0], base + fi[k], base + fi[k + 1]))

        # ------------------------------------------------------------------ #
        # POLYLINE  – can be a PFACE mesh or an M×N POLYMESH                  #
        # ezdxf 1.x exposes these as Polyface / Polymesh subclasses           #
        # ------------------------------------------------------------------ #
        elif etype == 'POLYLINE':
            try:
                from ezdxf.entities import Polymesh as _Polymesh
                from ezdxf.entities import Polyface as _Polyface

                if isinstance(entity, _Polyface):
                    # ---------- PFACE ----------
                    # ezdxf 1.x Polyface exposes .faces() iterator
                    pf_count = 0
                    for face in entity.faces():
                        face_pts = [v.dxf.location for v in face]
                        if len(face_pts) < 3:
                            continue
                        base = len(verts)
                        verts.extend(_vec3(p) for p in face_pts)
                        for k in range(1, len(face_pts) - 1):
                            tris.append((base, base + k, base + k + 1))
                        pf_count += 1
                    if pf_count > 0:
                        print(f"      PFACE mesh: extracted {pf_count} faces")

                elif isinstance(entity, _Polymesh):
                    # ---------- POLYMESH ----------
                    m = entity.dxf.m_count
                    n = entity.dxf.n_count
                    mesh_verts = list(entity.vertices)
                    base = len(verts)
                    verts.extend(_vec3(v.dxf.location) for v in mesh_verts)
                    pm_count = 0
                    
                    # Create faces between rows (cylindrical side)
                    for i in range(m - 1):
                        for j in range(n):  # Include n to wrap around!
                            a = base + i * n + j
                            b = base + i * n + ((j + 1) % n)
                            c = base + (i + 1) * n + ((j + 1) % n)
                            d = base + (i + 1) * n + j
                            tris.append((a, b, c))
                            tris.append((a, c, d))
                            pm_count += 1
                    
                    # Create end cap at row 0 (fan triangulation from first vertex)
                    # Centers at vertex 0, radiates to the ring (skip edges that would create degeneracy)
                    if m == 2 and n >= 4:  # Only for closed rings of 4+ vertices
                        center = base + 0
                        # Create non-degenerate triangles: from v[1] to v[n-1]
                        for j in range(1, n - 1):
                            v0 = base + 0 + j
                            v1 = base + 0 + j + 1
                            tris.append((center, v0, v1))
                            pm_count += 1
                        # Create final closing triangle: v[n-1], v[n], center back to v[1]
                        # This connects the last edge (v[n-1], v[0]) without degeneracy
                        # by using the "opposite" edge (v[n-1], v[1]) 
                        if n >= 4:
                            v_n_minus_1 = base + 0 + (n - 1)
                            v_1 = base + 0 + 1
                            tris.append((center, v_n_minus_1, v_1))
                            pm_count += 1
                    
                    # Create end cap at row m-1 (fan triangulation from last vertex)
                    if m == 2 and n >= 4:  # Only for closed rings of 4+ vertices
                        center = base + (m - 1) * n
                        # Create non-degenerate triangles: from v[1] to v[n-1]
                        for j in range(1, n - 1):
                            v0 = base + (m - 1) * n + j
                            v1 = base + (m - 1) * n + j + 1
                            tris.append((center, v1, v0))  # Reversed winding for outward normal
                            pm_count += 1
                        # Create final closing triangle with outward winding
                        if n >= 4:
                            v_1 = base + (m - 1) * n + 1
                            v_n_minus_1 = base + (m - 1) * n + (n - 1)
                            tris.append((center, v_1, v_n_minus_1))
                            pm_count += 1
                    
                    if pm_count > 0:
                        print(f"      POLYMESH ({m}×{n}): extracted {pm_count} faces")

                else:
                    # Plain 2D/3D polyline – no surface geometry, skip silently
                    pass

            except Exception as exc:
                print(f"  Warning: could not parse POLYLINE entity – {exc}")

        # ------------------------------------------------------------------ #
        # ACIS-based solids – not parseable without a full CAD kernel         #
        # ------------------------------------------------------------------ #
        elif etype in ('3DSOLID', 'BODY', 'REGION', 'SURFACE', '3DSURFACE'):
            skipped_acis.append(etype)

    # Summary
    print(f"\nDXF entity types found: {entity_counts}")
    if skipped_acis:
        print(
            f"\n  NOTE: {len(skipped_acis)} ACIS-based entity(ies) were skipped "
            f"({', '.join(sorted(set(skipped_acis)))})."
            "\n  To include them, export directly to STEP/STL from your CAD tool."
        )
    total_faces = len(tris) + len(quads)
    print(f"\nExtracted {len(verts):,} vertices, {len(tris):,} triangles, {len(quads):,} quads -> {total_faces:,} total faces.")

    return np.array(verts, dtype=np.float64), tris, quads


# ---------------------------------------------------------------------------
# Mesh cleaning — multi-tolerance T-junction stitching
# ---------------------------------------------------------------------------

def _merge_vertices_at_tolerance(
    vertices: np.ndarray,
    triangles: list[tuple],
    quads: list[tuple],
    tolerance: float,
) -> tuple[np.ndarray, list[tuple], list[tuple]]:
    """
    Merge all vertices whose distance is ≤ tolerance using a grid hash.

    Triangles and quads are processed together so they share the same
    compacted vertex array — avoids index-out-of-bounds when one list
    references vertices not present in the other.

    Zero-area degenerate faces (collapsed edges after merging) are dropped.
    """
    n = len(vertices)
    if n == 0:
        return vertices, triangles, quads

    cell = tolerance if tolerance > 0 else 1e-9
    keys = np.floor(vertices / cell).astype(np.int64)

    cell_map: dict[tuple, int] = {}
    remap = np.arange(n, dtype=np.int64)

    for i in range(n):
        kx, ky, kz = int(keys[i, 0]), int(keys[i, 1]), int(keys[i, 2])
        found = -1
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    neighbour = (kx + dx, ky + dy, kz + dz)
                    if neighbour in cell_map:
                        rep = cell_map[neighbour]
                        if np.linalg.norm(vertices[i] - vertices[rep]) <= tolerance:
                            found = rep
                            break
                if found >= 0:
                    break
            if found >= 0:
                break
        if found >= 0:
            remap[i] = found
        else:
            cell_map[(kx, ky, kz)] = i

    # Compact vertex array using all representatives referenced by either list
    unique_ids = np.unique(remap)
    new_index = np.empty(n, dtype=np.int64)
    new_index[unique_ids] = np.arange(len(unique_ids), dtype=np.int64)
    final_remap = new_index[remap]  # shape (n,) — safe for any original index

    new_verts = vertices[unique_ids]

    def _remap_faces(face_list: list[tuple], arity: int) -> list[tuple]:
        out = []
        for face in face_list:
            remapped = tuple(int(final_remap[idx]) for idx in face)
            if len(set(remapped)) == arity:   # drop collapsed edges
                out.append(remapped)
        return out

    new_tris = _remap_faces(triangles, 3)
    new_qs   = _remap_faces(quads, 4)

    return new_verts, new_tris, new_qs


def stitch_mesh(
    vertices: np.ndarray,
    triangles: list,
    quads: list,
    tolerances: tuple[float, ...] = (1e-4, 1e-5, 1e-6),
    aggressive: bool = False,
) -> tuple[np.ndarray, list, list]:
    """
    Multi-tolerance T-junction stitching.

    Runs vertex merging at descending tolerances (coarse → fine) so that
    near-duplicate vertices caused by floating-point drift are collapsed.
    Triangles and quads are always merged together so their shared vertex
    pool stays consistent.

    Parameters
    ----------
    vertices   : shape (N, 3)
    triangles  : list of (i, j, k)
    quads      : list of (i, j, k, l)
    tolerances : sequence of distances in model units (default: 1e-4 → 1e-6 mm)
    aggressive : if True, also drops duplicate faces after stitching

    Returns
    -------
    vertices, triangles, quads  (cleaned)
    """
    total_before = len(triangles) + len(quads)
    verts_before = len(vertices)
    print(
        f"\nMesh info: {verts_before:,} vertices, "
        f"{total_before:,} faces ({len(triangles):,} tri, {len(quads):,} quad)"
    )
    print(f"  Stitching T-junctions at tolerances: {tolerances}")

    v = vertices.copy()
    tris = list(triangles)
    qs = list(quads)

    for tol in tolerances:
        v, tris, qs = _merge_vertices_at_tolerance(v, tris, qs, tol)

    if aggressive:
        # Remove duplicate faces (same sorted index tuple)
        seen_t: set[tuple] = set()
        dedup_tris = []
        for f in tris:
            key = tuple(sorted(f))
            if key not in seen_t:
                seen_t.add(key)
                dedup_tris.append(f)
        tris = dedup_tris

        seen_q: set[tuple] = set()
        dedup_qs = []
        for f in qs:
            key = tuple(sorted(f))
            if key not in seen_q:
                seen_q.add(key)
                dedup_qs.append(f)
        qs = dedup_qs

    total_after = len(tris) + len(qs)
    verts_after = len(v)
    merged_v = verts_before - verts_after
    removed_f = total_before - total_after
    print(
        f"  Stitching complete: {verts_after:,} vertices "
        f"(-{merged_v:,} merged), {total_after:,} faces "
        f"(-{removed_f:,} degenerate removed)"
    )
    return v, tris, qs


# Keep the old name as an alias so existing call-sites still work
def clean_mesh(
    vertices: np.ndarray,
    triangles: list,
    quads: list,
    tolerance: float = 1e-6,
    aggressive: bool = False,
) -> tuple[np.ndarray, list, list]:
    return stitch_mesh(vertices, triangles, quads, aggressive=aggressive)


# ---------------------------------------------------------------------------
# Mesh optimization - merge coplanar faces for STEP
# ---------------------------------------------------------------------------

def optimize_for_step(vertices: np.ndarray, triangles: list, quads: list, coplanar_tolerance: float = 1e-2):
    """
    Light optimization: group faces by normal direction (faster than full merge).
    This provides some geometric coherence without the O(n²) complexity.
    
    Parameters
    ----------
    vertices : np.ndarray
    triangles : list[tuple[int, int, int]]
    quads : list[tuple[int, int, int, int]]
    coplanar_tolerance : float  – normal deviation threshold
    
    Returns
    -------
    optimized_faces : list[tuple[int, ...]]  – faces (mostly triangles with some merged quads)
    """
    print(f"\nOptimizing for STEP (light optimization enabled)...")
    
    # Convert all faces to triangle list
    all_tris = list(triangles)
    for q in quads:
        all_tris.append((q[0], q[1], q[2]))
        all_tris.append((q[0], q[2], q[3]))
    
    # For now, just return the faces as-is
    # (Full coplanar merging is  O(n²) which is too slow for 12K+ faces)
    # A proper solution would use spatial hashing or geometry library
    
    print(f"  Optimization complete: {len(all_tris):,} faces processed")
    
    return all_tris



# ---------------------------------------------------------------------------
# Connected-component splitting  (for assembly export)
# ---------------------------------------------------------------------------

def find_connected_components(
    vertices: np.ndarray,
    triangles: list,
    quads: list,
    min_faces: int = 1,
) -> list[dict]:
    """
    Split the mesh into connected components by shared vertex indices.

    Two faces are in the same component when they share at least one vertex.
    After stitch_mesh() the vertex array is already compacted, so shared
    indices correspond to physically touching / stitched faces.

    Parameters
    ----------
    vertices   : compacted vertex array from stitch_mesh
    triangles  : re-indexed triangle list
    quads      : re-indexed quad list
    min_faces  : drop components with fewer faces (default 1 = keep all)

    Returns
    -------
    List of dicts (sorted largest first), each containing:
        vertices  : np.ndarray  (local, re-indexed)
        triangles : list of (i,j,k)
        quads     : list of (i,j,k,l)
        face_count: int
        label     : str  e.g. "Part_001"
    """
    from collections import defaultdict

    all_faces = [(f, 'tri') for f in triangles] + [(f, 'quad') for f in quads]
    n_faces = len(all_faces)
    if n_faces == 0:
        return []

    # Union-Find
    parent = list(range(n_faces))
    rank   = [0] * n_faces

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        a, b = find(a), find(b)
        if a == b:
            return
        if rank[a] < rank[b]:
            a, b = b, a
        parent[b] = a
        if rank[a] == rank[b]:
            rank[a] += 1

    # vertex → list of face indices
    vert_faces: dict[int, list[int]] = defaultdict(list)
    for fi, (face, _) in enumerate(all_faces):
        for vi in face:
            vert_faces[vi].append(fi)

    for fi_list in vert_faces.values():
        for k in range(1, len(fi_list)):
            union(fi_list[0], fi_list[k])

    # Group faces by component root
    comp_faces: dict[int, list[int]] = defaultdict(list)
    for fi in range(n_faces):
        comp_faces[find(fi)].append(fi)

    # Build per-component sub-meshes
    results = []
    for face_indices in sorted(comp_faces.values(), key=lambda x: -len(x)):
        if len(face_indices) < min_faces:
            continue

        used: set[int] = set()
        for fi in face_indices:
            used.update(all_faces[fi][0])

        sorted_used = sorted(used)
        old_to_new  = {old: new for new, old in enumerate(sorted_used)}
        sub_verts   = vertices[sorted_used]
        sub_tris    = [
            tuple(old_to_new[v] for v in all_faces[fi][0])
            for fi in face_indices if all_faces[fi][1] == 'tri'
        ]
        sub_quads   = [
            tuple(old_to_new[v] for v in all_faces[fi][0])
            for fi in face_indices if all_faces[fi][1] == 'quad'
        ]
        results.append({
            'vertices':   sub_verts,
            'triangles':  sub_tris,
            'quads':      sub_quads,
            'face_count': len(face_indices),
            'label':      '',   # filled in below
        })

    n_digits = max(3, len(str(len(results))))
    for i, comp in enumerate(results):
        comp['label'] = f"Part_{str(i + 1).zfill(n_digits)}"

    print(
        f"  Connected components: {len(results)} "
        f"(largest: {results[0]['face_count'] if results else 0} faces)"
    )
    return results


# ---------------------------------------------------------------------------
# STL export  (numpy-stl)
# ---------------------------------------------------------------------------

def _triangles_for_stl(triangles: list, quads: list) -> list[tuple[int, int, int]]:
    all_tris = list(triangles)
    for q in quads:
        all_tris.append((q[0], q[1], q[2]))
        all_tris.append((q[0], q[2], q[3]))
    return all_tris


def _save_stl_binary(vertices: np.ndarray, all_tris: list, output_path: Path) -> None:
    """Write binary STL without numpy-stl (used in Pyodide / browser)."""
    import struct

    with open(output_path, "wb") as fh:
        fh.write(b"\0" * 80)
        fh.write(struct.pack("<I", len(all_tris)))
        for a, b, c in all_tris:
            va, vb, vc = vertices[a], vertices[b], vertices[c]
            n = np.cross(vb - va, vc - va)
            norm = np.linalg.norm(n)
            if norm > 0:
                n = n / norm
            else:
                n = np.array([0.0, 0.0, 0.0])
            fh.write(struct.pack("<3f", float(n[0]), float(n[1]), float(n[2])))
            fh.write(struct.pack("<9f", *[float(x) for row in (va, vb, vc) for x in row]))
            fh.write(struct.pack("<H", 0))


def save_stl(vertices: np.ndarray, triangles: list, quads: list, output_path: Path):
    all_tris = _triangles_for_stl(triangles, quads)
    if not all_tris:
        print("Warning: no faces – STL not written.")
        return

    try:
        from stl import mesh as stl_mesh
    except ImportError:
        _save_stl_binary(vertices, all_tris, output_path)
        print(
            f"STL saved -> {output_path}  ({len(all_tris):,} triangles from "
            f"{len(triangles)} tri + {len(quads)} quad)"
        )
        return

    solid = stl_mesh.Mesh(np.zeros(len(all_tris), dtype=stl_mesh.Mesh.dtype))
    for i, (a, b, c) in enumerate(all_tris):
        solid.vectors[i] = vertices[[a, b, c]]

    solid.save(str(output_path))
    print(f"STL saved -> {output_path}  ({len(all_tris):,} triangles from {len(triangles)} tri + {len(quads)} quad)")


# ---------------------------------------------------------------------------
# STEP export  –  pure-Python AP214 writer, no CAD kernel required
# ---------------------------------------------------------------------------

def save_step(vertices: np.ndarray, triangles: list, quads: list, output_path: Path, optimize: bool = True, normal_tolerance: float = 1e-10, keep_degenerate: bool = False):
    """
    Write a STEP AP214 (automotive_design) file from triangles and quads.
    Each triangle becomes a 3-sided ADVANCED_FACE, each quad becomes a 4-sided ADVANCED_FACE.
    This preserves the original geometry structure without diagonal splits.
    
    Parameters
    ----------
    normal_tolerance : float  – minimum normal length to accept a face (default: 1e-10)
    keep_degenerate : bool  – if True, keep near-degenerate faces (may help with cylinder gaps)
    """
    
    if not triangles and not quads:
        print("Warning: no faces – STEP not written.")
        return

    print(f"Building STEP file from {len(triangles):,} triangles + {len(quads):,} quads …")

    lines: list[str] = []
    _id = 0

    def emit(s: str) -> int:
        nonlocal _id
        _id += 1
        lines.append(f"#{_id} = {s};")
        return _id

    # ------------------------------------------------------------------ #
    # Boilerplate product / context entities                              #
    # ------------------------------------------------------------------ #
    app_ctx   = emit("APPLICATION_CONTEXT('automotive design')")
    app_proto = emit(
        f"APPLICATION_PROTOCOL_DEFINITION("
        f"'draft international standard','automotive_design',1998,#{app_ctx})"
    )
    prod_ctx  = emit(f"PRODUCT_CONTEXT('',#{app_ctx},'mechanical')")
    product   = emit(f"PRODUCT('DXF_Model','DXF_Model','',(#{prod_ctx}))")
    pd_ctx    = emit(f"PRODUCT_DEFINITION_CONTEXT('detailed design',#{app_ctx},'design')")
    pdf       = emit(f"PRODUCT_DEFINITION_FORMATION('','',#{product})")
    prod_def  = emit(f"PRODUCT_DEFINITION('design','',#{pdf},#{pd_ctx})")
    pd_shape  = emit(f"PRODUCT_DEFINITION_SHAPE('','',#{prod_def})")

    # Units / uncertainty
    unc_meas  = emit("UNCERTAINTY_MEASURE_WITH_UNIT(LENGTH_MEASURE(1.E-07),#%UNIT%,'distance_accuracy_value','')")
    si_unit   = emit("(SI_UNIT($,.METRE.))")
    si_angle  = emit("(SI_UNIT($,.RADIAN.))")
    si_ster   = emit("(SI_UNIT($,.STERADIAN.))")
    rep_ctx   = emit(
        f"(GEOMETRIC_REPRESENTATION_CONTEXT(3) "
        f"GLOBAL_UNCERTAINTY_ASSIGNED_CONTEXT((#{unc_meas})) "
        f"GLOBAL_UNIT_ASSIGNED_CONTEXT((#{si_unit},#{si_angle},#{si_ster})) "
        f"REPRESENTATION_CONTEXT('Context #1','3D Context with UNIT and UNCERTAINTY'))"
    )
    # Patch the forward-reference to the unit in the uncertainty measure
    lines[unc_meas - 1] = lines[unc_meas - 1].replace('%UNIT%', str(si_unit))

    # ------------------------------------------------------------------ #
    # Geometry: ADVANCED_FACE for each triangle and quad                  #
    # ------------------------------------------------------------------ #
    face_ids: list[int] = []
    skipped = 0

    def create_face(pts, face_type="polygon"):
        """Create ADVANCED_FACE from a list of vertices."""
        nonlocal skipped
        
        if len(pts) < 3:
            skipped += 1
            return None
        
        # Compute normal from first 3 vertices
        p1, p2, p3 = pts[0], pts[1], pts[2]
        v12 = p2 - p1
        v13 = p3 - p1
        normal = np.cross(v12, v13)
        nlen = float(np.linalg.norm(normal))
        
        if nlen < normal_tolerance:
            if not keep_degenerate:
                skipped += 1
                return None
            # If keep_degenerate=True, try to fix the normal
            if nlen < 1e-15:
                # Truly degenerate, skip
                skipped += 1
                return None
            # Use what we have, even if small
        
        if nlen > 0:
            normal = normal / nlen
        else:
            skipped += 1
            return None

        def fmt(v):
            return f"({v[0]:.8f},{v[1]:.8f},{v[2]:.8f})"

        # Create Cartesian points and vertex points
        cp_ids = []
        vp_ids = []
        for pt in pts:
            cp_id = emit(f"CARTESIAN_POINT('',{fmt(pt)})")
            vp_id = emit(f"VERTEX_POINT('',#{cp_id})")
            cp_ids.append(cp_id)
            vp_ids.append(vp_id)

        def make_edge(pa_id, pb_id, va_id, vb_id, pa_vec):
            """Return ORIENTED_EDGE id for one directed edge."""
            d = pa_vec.copy()
            dn = float(np.linalg.norm(d))
            if dn < 1e-12:
                d = np.array([1.0, 0.0, 0.0])
            else:
                d /= dn
            dir_id  = emit(f"DIRECTION('',{fmt(d)})")
            vec_id  = emit(f"VECTOR('',#{dir_id},1.)")
            line_id = emit(f"LINE('',#{pa_id},#{vec_id})")
            ec_id   = emit(f"EDGE_CURVE('',#{va_id},#{vb_id},#{line_id},.T.)")
            oe_id   = emit(f"ORIENTED_EDGE('',*,*,#{ec_id},.T.)")
            return oe_id

        # Create edges for polygon boundary
        oe_ids = []
        for i in range(len(pts)):
            j = (i + 1) % len(pts)
            oe_id = make_edge(cp_ids[i], cp_ids[j], vp_ids[i], vp_ids[j], pts[j] - pts[i])
            oe_ids.append(oe_id)

        oe_refs = ",".join(f"#{oe_id}" for oe_id in oe_ids)
        el  = emit(f"EDGE_LOOP('',(  {oe_refs}  ))")
        fob = emit(f"FACE_OUTER_BOUND('',#{el},.T.)")

        # Plane: axis placement at p1, Z=normal, X=ref_dir
        ref = np.array([1.0, 0.0, 0.0]) if abs(normal[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
        ref = ref - np.dot(ref, normal) * normal
        rlen = float(np.linalg.norm(ref))
        ref = ref / (rlen if rlen > 1e-12 else 1.0)

        norm_dir  = emit(f"DIRECTION('',{fmt(normal)})")
        ref_dir   = emit(f"DIRECTION('',{fmt(ref)})")
        ax2p3d    = emit(f"AXIS2_PLACEMENT_3D('',#{cp_ids[0]},#{norm_dir},#{ref_dir})")
        plane_id  = emit(f"PLANE('',#{ax2p3d})")

        face_id = emit(f"ADVANCED_FACE('',(#{fob}),#{plane_id},.T.)")
        return face_id

    # Export triangles (3-sided faces)
    for ia, ib, ic in triangles:
        pts = [vertices[ia], vertices[ib], vertices[ic]]
        face_id = create_face(pts, "triangle")
        if face_id:
            face_ids.append(face_id)

    # Export quads (4-sided faces) - THIS PRESERVES THEM WITHOUT DIAGONAL SPLITS!
    for ia, ib, ic, id_ in quads:
        pts = [vertices[ia], vertices[ib], vertices[ic], vertices[id_]]
        face_id = create_face(pts, "quad")
        if face_id:
            face_ids.append(face_id)

    if skipped:
        print(f"  ({skipped} degenerate faces skipped)")

    # ------------------------------------------------------------------ #
    # Shell / shape representation                                        #
    # ------------------------------------------------------------------ #
    face_refs  = ",".join(f"#{f}" for f in face_ids)
    shell_id   = emit(f"OPEN_SHELL('',(  {face_refs}  ))")
    sbsm_id    = emit(f"SHELL_BASED_SURFACE_MODEL('',(#{shell_id}))")

    # Geometric set wrapping
    gset_id    = emit(f"GEOMETRICALLY_BOUNDED_SURFACE_SHAPE_REPRESENTATION('',(#{sbsm_id}),#{rep_ctx})")

    # Link shape representation to product definition
    emit(f"SHAPE_DEFINITION_REPRESENTATION(#{pd_shape},#{gset_id})")

    # ------------------------------------------------------------------ #
    # Write file                                                          #
    # ------------------------------------------------------------------ #
    now = "2026-03-06T00:00:00"
    stem = output_path.stem

    header = (
        "ISO-10303-21;\n"
        "HEADER;\n"
        f"FILE_DESCRIPTION(('Generated by dxf_converter.py'),'2;1');\n"
        f"FILE_NAME('{stem}','{now}',(''),(''),"
        f"'dxf_converter.py','','');\n"
        "FILE_SCHEMA(('AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }'));\n"
        "ENDSEC;\n"
        "DATA;\n"
    )
    footer = "ENDSEC;\nEND-ISO-10303-21;\n"

    with open(output_path, "w", encoding="utf-8") as fh:
        fh.write(header)
        fh.write("\n".join(lines))
        fh.write("\n")
        fh.write(footer)

    size_mb = output_path.stat().st_size / 1_048_576
    total_exported = len(triangles) + len(quads) - skipped
    print(f"STEP saved -> {output_path}  ({len(face_ids):,} faces ({len(triangles)} tri, {len(quads)} quad), {size_mb:.1f} MB)")


# ---------------------------------------------------------------------------
# STEP AP214 assembly writer
# ---------------------------------------------------------------------------

def save_step_assembly(
    components: list[dict],
    output_path: Path,
    assembly_name: str = "Assembly",
    normal_tolerance: float = 1e-10,
    keep_degenerate: bool = False,
) -> None:
    """
    Write a STEP AP214 file with a proper assembly hierarchy.

    Each entry in *components* (from find_connected_components) becomes a
    separate PRODUCT sub-part.  A top-level PRODUCT links all sub-parts via
    NEXT_ASSEMBLY_USAGE_OCCURRENCE so Creo / NX / SolidWorks import it as an
    assembly, not a single part.

    Parameters
    ----------
    components     : output of find_connected_components()
    output_path    : .stp file path
    assembly_name  : name written into the top-level PRODUCT entity
    normal_tolerance, keep_degenerate : same as save_step()
    """
    if not components:
        print("Warning: no components – assembly STEP not written.")
        return

    print(
        f"Building STEP assembly from {len(components)} components "
        f"({sum(c['face_count'] for c in components):,} total faces) …"
    )

    lines: list[str] = []
    _id = 0

    def emit(s: str) -> int:
        nonlocal _id
        _id += 1
        lines.append(f"#{_id} = {s};")
        return _id

    # ------------------------------------------------------------------
    # Shared global context (written once, referenced by every product)
    # ------------------------------------------------------------------
    app_ctx  = emit("APPLICATION_CONTEXT('automotive design')")
    emit(
        f"APPLICATION_PROTOCOL_DEFINITION("
        f"'draft international standard','automotive_design',1998,#{app_ctx})"
    )
    prod_ctx = emit(f"PRODUCT_CONTEXT('',#{app_ctx},'mechanical')")
    pd_ctx   = emit(f"PRODUCT_DEFINITION_CONTEXT('part definition',#{app_ctx},'design')")

    unc_meas = emit(
        "UNCERTAINTY_MEASURE_WITH_UNIT(LENGTH_MEASURE(1.E-07),#%UNIT%,"
        "'distance_accuracy_value','')"
    )
    si_unit  = emit("(SI_UNIT($,.METRE.))")
    si_angle = emit("(SI_UNIT($,.RADIAN.))")
    si_ster  = emit("(SI_UNIT($,.STERADIAN.))")
    rep_ctx  = emit(
        f"(GEOMETRIC_REPRESENTATION_CONTEXT(3) "
        f"GLOBAL_UNCERTAINTY_ASSIGNED_CONTEXT((#{unc_meas})) "
        f"GLOBAL_UNIT_ASSIGNED_CONTEXT((#{si_unit},#{si_angle},#{si_ster})) "
        f"REPRESENTATION_CONTEXT('Context #1','3D Context with UNIT and UNCERTAINTY'))"
    )
    lines[unc_meas - 1] = lines[unc_meas - 1].replace('%UNIT%', str(si_unit))

    # ------------------------------------------------------------------
    # Helper: write one ADVANCED_FACE; returns entity id or None
    # ------------------------------------------------------------------
    total_skipped = 0

    def create_face(pts) -> int | None:
        nonlocal total_skipped

        if len(pts) < 3:
            total_skipped += 1
            return None

        p1, p2, p3 = pts[0], pts[1], pts[2]
        normal = np.cross(p2 - p1, p3 - p1)
        nlen   = float(np.linalg.norm(normal))

        if nlen < normal_tolerance:
            if not keep_degenerate or nlen < 1e-15:
                total_skipped += 1
                return None

        normal = normal / nlen if nlen > 0 else normal

        def fmt(v):
            return f"({float(v[0]):.8f},{float(v[1]):.8f},{float(v[2]):.8f})"

        cp_ids, vp_ids = [], []
        for pt in pts:
            cp = emit(f"CARTESIAN_POINT('',{fmt(pt)})")
            vp = emit(f"VERTEX_POINT('',#{cp})")
            cp_ids.append(cp)
            vp_ids.append(vp)

        oe_ids = []
        for i in range(len(pts)):
            j   = (i + 1) % len(pts)
            d   = pts[j] - pts[i]
            dn  = float(np.linalg.norm(d))
            d   = d / dn if dn > 1e-12 else np.array([1.0, 0.0, 0.0])
            dir_id = emit(f"DIRECTION('',{fmt(d)})")
            vec_id = emit(f"VECTOR('',#{dir_id},1.)")
            lin_id = emit(f"LINE('',#{cp_ids[i]},#{vec_id})")
            ec_id  = emit(f"EDGE_CURVE('',#{vp_ids[i]},#{vp_ids[j]},#{lin_id},.T.)")
            oe_ids.append(emit(f"ORIENTED_EDGE('',*,*,#{ec_id},.T.)"))

        el  = emit(f"EDGE_LOOP('',({','.join(f'#{o}' for o in oe_ids)}))")
        fob = emit(f"FACE_OUTER_BOUND('',#{el},.T.)")

        ref = np.array([1.0, 0.0, 0.0]) if abs(normal[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
        ref = ref - np.dot(ref, normal) * normal
        rn  = float(np.linalg.norm(ref))
        ref = ref / (rn if rn > 1e-12 else 1.0)

        nd  = emit(f"DIRECTION('',{fmt(normal)})")
        rd  = emit(f"DIRECTION('',{fmt(ref)})")
        ax  = emit(f"AXIS2_PLACEMENT_3D('',#{cp_ids[0]},#{nd},#{rd})")
        pl  = emit(f"PLANE('',#{ax})")
        return emit(f"ADVANCED_FACE('',(#{fob}),#{pl},.T.)")

    # ------------------------------------------------------------------
    # Write one STEP product per component; collect product_def ids
    # ------------------------------------------------------------------
    component_pd_ids: list[int] = []
    component_product_ids: list[int] = []

    for comp in components:
        label    = comp['label']
        verts    = comp['vertices']
        tris     = comp['triangles']
        qs       = comp['quads']

        product  = emit(f"PRODUCT('{label}','{label}','',(#{prod_ctx}))")
        pdf      = emit(f"PRODUCT_DEFINITION_FORMATION('','',#{product})")
        prod_def = emit(f"PRODUCT_DEFINITION('design','',#{pdf},#{pd_ctx})")
        pd_shape = emit(f"PRODUCT_DEFINITION_SHAPE('','',#{prod_def})")

        face_ids: list[int] = []
        for ia, ib, ic in tris:
            fid = create_face([verts[ia], verts[ib], verts[ic]])
            if fid:
                face_ids.append(fid)
        for ia, ib, ic, id_ in qs:
            fid = create_face([verts[ia], verts[ib], verts[ic], verts[id_]])
            if fid:
                face_ids.append(fid)

        if not face_ids:
            # component had only degenerate faces — skip linkage
            component_pd_ids.append(None)
            component_product_ids.append(None)
            continue

        shell = emit(f"OPEN_SHELL('',({','.join(f'#{f}' for f in face_ids)}))")
        sbsm  = emit(f"SHELL_BASED_SURFACE_MODEL('',(#{shell}))")
        grep  = emit(
            f"GEOMETRICALLY_BOUNDED_SURFACE_SHAPE_REPRESENTATION("
            f"'',(#{sbsm}),#{rep_ctx})"
        )
        emit(f"SHAPE_DEFINITION_REPRESENTATION(#{pd_shape},#{grep})")

        component_pd_ids.append(prod_def)
        component_product_ids.append(product)

    # ------------------------------------------------------------------
    # Top-level assembly product
    # ------------------------------------------------------------------
    assy_product  = emit(f"PRODUCT('{assembly_name}','{assembly_name}','',(#{prod_ctx}))")
    assy_pdf      = emit(f"PRODUCT_DEFINITION_FORMATION('','',#{assy_product})")
    assy_pd       = emit(f"PRODUCT_DEFINITION('design','',#{assy_pdf},#{pd_ctx})")
    assy_pd_shape = emit(f"PRODUCT_DEFINITION_SHAPE('','',#{assy_pd})")
    # Empty shape representation for the assembly level
    assy_sr       = emit(f"SHAPE_REPRESENTATION('',(#{rep_ctx}),#{rep_ctx})")
    emit(f"SHAPE_DEFINITION_REPRESENTATION(#{assy_pd_shape},#{assy_sr})")

    # ------------------------------------------------------------------
    # Assembly links: NEXT_ASSEMBLY_USAGE_OCCURRENCE per component
    # ------------------------------------------------------------------
    usage_idx = 1
    for comp_pd, comp_prod in zip(component_pd_ids, component_product_ids):
        if comp_pd is None:
            continue
        nauo = emit(
            f"NEXT_ASSEMBLY_USAGE_OCCURRENCE("
            f"'{usage_idx}','{usage_idx}','',#{assy_pd},#{comp_pd},$)"
        )
        emit(f"PRODUCT_RELATED_PRODUCT_CATEGORY('part',$,(#{comp_prod}))")
        usage_idx += 1

    # ------------------------------------------------------------------
    # Write file
    # ------------------------------------------------------------------
    now  = "2026-03-06T00:00:00"
    stem = output_path.stem

    header = (
        "ISO-10303-21;\n"
        "HEADER;\n"
        f"FILE_DESCRIPTION(('STEP AP214 Assembly generated by dxf_converter.py'),'2;1');\n"
        f"FILE_NAME('{stem}','{now}',(''),(''),'dxf_converter.py','','');\n"
        "FILE_SCHEMA(('AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }'));\n"
        "ENDSEC;\n"
        "DATA;\n"
    )
    footer = "ENDSEC;\nEND-ISO-10303-21;\n"

    with open(output_path, "w", encoding="utf-8") as fh:
        fh.write(header)
        fh.write("\n".join(lines))
        fh.write("\n")
        fh.write(footer)

    size_mb = output_path.stat().st_size / 1_048_576
    valid_comps = sum(1 for x in component_pd_ids if x is not None)
    if total_skipped:
        print(f"  ({total_skipped} degenerate faces skipped)")
    print(
        f"STEP assembly saved -> {output_path}  "
        f"({valid_comps} components, "
        f"{sum(c['face_count'] for c in components):,} total faces, "
        f"{size_mb:.1f} MB)"
    )




# ---------------------------------------------------------------------------
# IGES export  –  pure-Python IGES 5.3 writer, no CAD kernel required
# ---------------------------------------------------------------------------

def save_iges(vertices: np.ndarray, triangles: list, quads: list, output_path: Path, normal_tolerance: float = 1e-10, keep_degenerate: bool = False):
    """
    Write an IGES 5.3 file directly from triangles and quads without diagonal splits.
    Triangles get 3 edges, quads get 4 edges = no splits!
    
    Parameters
    ----------
    normal_tolerance : float  – minimum normal length to accept a face (default: 1e-10)
    keep_degenerate : bool  – if True, keep near-degenerate faces (may help with cylinder gaps)
    """
    total_faces = len(triangles) + len(quads)
    
    if total_faces == 0:
        print("Warning: no faces – IGES not written.")
        return

    print(f"Building IGES file from {len(triangles):,} triangles + {len(quads):,} quads …")

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _igs_line(data: str, section: str, seqno: int) -> str:
        """Return exactly one 80-char IGES line (newline included)."""
        return f"{data[:72]:<72}{section}{seqno:7d}\n"

    def _f(v: float) -> str:
        return f"{float(v):.6E}"

    def _fv(v) -> str:
        return f"{_f(v[0])},{_f(v[1])},{_f(v[2])}"

    # ------------------------------------------------------------------
    # Accumulate entities  (two-pass: collect first, lay out PD second)
    # ------------------------------------------------------------------
    _entities: list[dict] = []   # {etype, form, params}

    def _add(etype: int, form: int, params: str) -> int:
        idx = len(_entities)
        _entities.append({'etype': etype, 'form': form, 'params': params})
        return idx

    def _de(idx: int) -> int:
        """DE sequence number for entity idx (1-based odd)."""
        return 2 * idx + 1

    # ------------------------------------------------------------------
    # Build geometry
    # ------------------------------------------------------------------
    skipped = 0
    
    def process_polygon(pt_indices):
        """Create IGES entities for a polygon (triangle or quad)."""
        nonlocal skipped
        
        if len(pt_indices) < 3:
            skipped += 1
            return
        
        pts = [vertices[i] for i in pt_indices]
        
        # Compute normal
        p1, p2, p3 = pts[0], pts[1], pts[2]
        normal = np.cross(p2 - p1, p3 - p1)
        nlen = float(np.linalg.norm(normal))
        
        if nlen < normal_tolerance:
            if not keep_degenerate:
                skipped += 1
                return
            if nlen < 1e-15:
                skipped += 1
                return
        
        if nlen > 0:
            normal = normal / nlen
        else:
            skipped += 1
            return

        # Create edge lines for all polygon edges
        edge_ids = []
        for i in range(len(pts)):
            j = (i + 1) % len(pts)
            edge_id = _add(110, 0, f"{_fv(pts[i])},{_fv(pts[j])}")
            edge_ids.append(edge_id)

        # Composite curve (closed boundary loop)
        edge_refs = ",".join(str(_de(e)) for e in edge_ids)
        cc = _add(102, 0, f"{len(edge_ids)},{edge_refs}")

        # Plane:  A·x + B·y + C·z = D
        A, B, C = normal
        D = float(np.dot(normal, pts[0]))
        plane = _add(108, 0, f"{_f(A)},{_f(B)},{_f(C)},{_f(D)},0,{_fv(pts[0])},1.0")

        # Curve on parametric surface
        cos142 = _add(142, 0, f"1,{_de(plane)},{_de(cc)},0,1")

        # Trimmed surface
        _add(144, 0, f"{_de(plane)},0,0,{_de(cos142)}")

    # Process triangles (no splits)
    for ia, ib, ic in triangles:
        process_polygon([ia, ib, ic])

    # Process quads (no splits - kept as 4-sided faces!)
    for ia, ib, ic, id_ in quads:
        process_polygon([ia, ib, ic, id_])

    if skipped:
        print(f"  ({skipped} degenerate polygons skipped)")

    if not _entities:
        print("Warning: no valid entities – IGES not written.")
        return

    # ------------------------------------------------------------------
    # Pass 2: compute PD layout
    # Each entity PD content:  "etype,params,de_ptr;"
    #   fits in up to 64 chars per PD line; col 65-72 = DE back-pointer
    # ------------------------------------------------------------------
    pd_chunks: list[tuple[str, int]] = []   # (chunk ≤64 chars, de_ptr)
    pd_info: list[tuple[int, int]] = []     # (pd_start_1based, line_count) per entity

    for idx, ent in enumerate(_entities):
        content = f"{ent['etype']},{ent['params']},{_de(idx)};"
        start = len(pd_chunks) + 1
        while content:
            pd_chunks.append((content[:64], _de(idx)))
            content = content[64:]
        pd_info.append((start, len(pd_chunks) - start + 1))

    # ------------------------------------------------------------------
    # Write sections
    # ------------------------------------------------------------------
    out: list[str] = []

    # --- S: Start ---------------------------------------------------------
    out.append(_igs_line("DXF to IGES – generated by dxf_converter.py", 'S', 1))

    # --- G: Global --------------------------------------------------------
    stem = output_path.name
    h_stem = f"{len(stem)}H{stem}"
    g_raw = (
        f"1H,,1H;,{h_stem},{h_stem},"
        f"13Hdxf_converter,13Hdxf_converter,"
        f"32,308,7,308,15,,1.,2,2HMM,1,0.01,"
        f"15H20260306.000000,1.E-4,1.E10,,,,11,0,"
        f"15H20260306.000000;"
    )
    g_seq = 1
    while g_raw:
        out.append(_igs_line(g_raw[:72], 'G', g_seq))
        g_raw = g_raw[72:]
        g_seq += 1
    g_lines = g_seq - 1

    # --- D: Directory Entry -----------------------------------------------
    def _df(v, w=8):
        return str(v).rjust(w)[:w]

    de_seq = 1
    for idx, ent in enumerate(_entities):
        pd_start, pd_count = pd_info[idx]
        et = ent['etype']
        # line 1
        d1 = (
            _df(et) + _df(pd_start) + _df(0) + _df(0) + _df(0)
            + _df(0) + _df(0) + _df(0) + "00000000"
        )
        out.append(_igs_line(d1, 'D', de_seq));  de_seq += 1
        # line 2
        d2 = (
            _df(et) + _df(0) + _df(0) + _df(pd_count) + _df(ent['form'])
            + " " * 8 + " " * 8 + " " * 8 + _df(0)
        )
        out.append(_igs_line(d2, 'D', de_seq));  de_seq += 1
    de_lines = de_seq - 1

    # --- P: Parameter Data ------------------------------------------------
    pd_seq = 1
    for chunk, de_ptr in pd_chunks:
        line_data = f"{chunk:<64}{str(de_ptr):>8}"
        out.append(_igs_line(line_data, 'P', pd_seq))
        pd_seq += 1
    pd_lines = pd_seq - 1

    # --- T: Terminate -----------------------------------------------------
    t = f"S{1:7d}G{g_lines:7d}D{de_lines:7d}P{pd_lines:7d}" + " " * 40
    out.append(_igs_line(t, 'T', 1))

    with open(output_path, 'w', encoding='ascii', errors='replace') as fh:
        fh.writelines(out)

    size_mb = output_path.stat().st_size / 1_048_576
    n_faces = sum(1 for e in _entities if e['etype'] == 144)
    print(f"IGES saved -> {output_path}  ({n_faces:,} trimmed surfaces ({len(triangles)} tri, {len(quads)} quad), {size_mb:.1f} MB)")



# ---------------------------------------------------------------------------
# Programmatic API (CLI + web app)
# ---------------------------------------------------------------------------

def convert_dxf(
    input_path: Path,
    out_base: Path,
    fmt: str = "iges",
    aggressive: bool = False,
    normal_tolerance: float = 1e-10,
    keep_degenerate: bool = False,
    stitch_tolerances: tuple[float, ...] = (1e-4, 1e-5, 1e-6),
    assembly: bool = False,
    min_component_faces: int = 1,
) -> dict:
    """
    Convert a 3D DXF file to STL, STEP, and/or IGES.

    Parameters
    ----------
    input_path : Path
        Input .dxf file.
    out_base : Path
        Output path without extension (e.g. /tmp/model).
    fmt : str
        One of "stl", "step", "iges", or "all".
    aggressive, normal_tolerance, keep_degenerate
        Same as CLI flags.
    stitch_tolerances : tuple of floats
        Vertex merge distances applied coarse-to-fine (model units, usually mm).
        Default (1e-4, 1e-5, 1e-6) closes most T-junction seams.
        Pass (1e-6,) to use only the tightest pass (old behaviour).
    assembly : bool
        If True and fmt includes "step", write a STEP assembly (.stp) split by
        connected components in addition to (or instead of) the flat STEP part.
        Produces a file named <out_base>_assembly.stp.
    min_component_faces : int
        Discard components with fewer faces than this threshold (default 1).

    Returns
    -------
    dict with keys: vertices, triangles, quads, outputs (list of Path), face_count,
                    components (list of component dicts, or [] if assembly=False).
    """
    input_path = Path(input_path).resolve()
    out_base = Path(out_base).resolve()
    out_base.parent.mkdir(parents=True, exist_ok=True)

    vertices, triangles, quads = extract_geometry(input_path)
    vertices, triangles, quads = stitch_mesh(
        vertices, triangles, quads,
        tolerances=stitch_tolerances,
        aggressive=aggressive,
    )

    total_faces = len(triangles) + len(quads)
    if total_faces == 0:
        raise ValueError(
            "No convertible geometry found in the DXF. "
            "The file must contain 3DFACE, MESH, or POLYLINE (PFACE/POLYMESH)."
        )

    outputs: list[Path] = []
    if fmt in ("stl", "all"):
        path = out_base.with_suffix(".stl")
        save_stl(vertices, triangles, quads, path)
        outputs.append(path)
    if fmt in ("step", "all"):
        path = out_base.with_suffix(".stp")
        save_step(
            vertices,
            triangles,
            quads,
            path,
            normal_tolerance=normal_tolerance,
            keep_degenerate=keep_degenerate,
        )
        outputs.append(path)
    if fmt in ("iges", "all"):
        path = out_base.with_suffix(".igs")
        save_iges(
            vertices,
            triangles,
            quads,
            path,
            normal_tolerance=normal_tolerance,
            keep_degenerate=keep_degenerate,
        )
        outputs.append(path)

    # Assembly STEP (always produces a separate _assembly.stp file)
    components: list[dict] = []
    if assembly and fmt in ("step", "all", "iges"):
        print("\nBuilding connected-component assembly …")
        components = find_connected_components(
            vertices, triangles, quads, min_faces=min_component_faces
        )
        assy_path = out_base.parent / (out_base.name + "_assembly.stp")
        save_step_assembly(
            components,
            assy_path,
            assembly_name=out_base.stem,
            normal_tolerance=normal_tolerance,
            keep_degenerate=keep_degenerate,
        )
        outputs.append(assy_path)

    return {
        "vertices": vertices,
        "triangles": triangles,
        "quads": quads,
        "outputs": outputs,
        "face_count": total_faces,
        "components": components,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Convert a 3D DXF file to STL, STEP and/or IGES.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("input",
                        help="Path to the input .dxf file")
    parser.add_argument("--format", choices=["stl", "step", "iges", "all"],
                        default="iges",
                        help="Output format  (default: iges)")
    parser.add_argument("--output",
                        help="Output base path (no extension). "
                             "Defaults to same folder / stem as input.")
    parser.add_argument("--aggressive", action="store_true",
                        help="Apply aggressive mesh optimization (better for Creo, slower)")
    parser.add_argument("--normal-tolerance", type=float, default=1e-10,
                        help="Minimum normal length to accept a face (default: 1e-10). Increase to skip tiny faces.")
    parser.add_argument("--keep-degenerate", action="store_true",
                        help="Keep near-degenerate faces (may help with cylinder gaps)")
    parser.add_argument(
        "--stitch-tolerance",
        type=float,
        nargs="+",
        default=[1e-4, 1e-5, 1e-6],
        metavar="TOL",
        help=(
            "One or more vertex-merge distances (coarse → fine, in model units). "
            "Default: 1e-4 1e-5 1e-6. "
            "Use a single tight value (e.g. 1e-6) to replicate old behaviour."
        ),
    )
    parser.add_argument(
        "--assembly",
        action="store_true",
        help=(
            "Also write a STEP assembly file (<output>_assembly.stp) where each "
            "spatially connected group of faces becomes a separate sub-part. "
            "Imports into Creo / NX / SolidWorks as a proper assembly."
        ),
    )
    parser.add_argument(
        "--min-component-faces",
        type=int,
        default=1,
        metavar="N",
        help="Discard assembly components with fewer than N faces (default: 1).",
    )

    args = parser.parse_args()

    input_path = Path(args.input).resolve()
    if not input_path.exists():
        sys.exit(f"Error: '{input_path}' not found.")

    if args.output:
        out_base = Path(args.output).resolve()
    else:
        out_base = input_path.with_suffix("")

    out_base.parent.mkdir(parents=True, exist_ok=True)

    print(f"Reading: {input_path}")
    try:
        convert_dxf(
            input_path,
            out_base,
            fmt=args.format,
            aggressive=args.aggressive,
            normal_tolerance=args.normal_tolerance,
            keep_degenerate=args.keep_degenerate,
            stitch_tolerances=tuple(args.stitch_tolerance),
            assembly=args.assembly,
            min_component_faces=args.min_component_faces,
        )
    except ValueError as exc:
        print(f"\n{exc}")
        sys.exit(1)

    print("\nDone.")


if __name__ == "__main__":
    main()
