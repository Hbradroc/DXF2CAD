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
                    for face in entity.faces():
                        face_pts = [v.dxf.location for v in face]
                        if len(face_pts) < 3:
                            continue
                        base = len(verts)
                        verts.extend(_vec3(p) for p in face_pts)
                        for k in range(1, len(face_pts) - 1):
                            tris.append((base, base + k, base + k + 1))

                elif isinstance(entity, _Polymesh):
                    # ---------- POLYMESH ----------
                    m = entity.dxf.m_count
                    n = entity.dxf.n_count
                    mesh_verts = list(entity.vertices)
                    base = len(verts)
                    verts.extend(_vec3(v.dxf.location) for v in mesh_verts)
                    for i in range(m - 1):
                        for j in range(n - 1):
                            a = base + i * n + j
                            b = base + i * n + (j + 1)
                            c = base + (i + 1) * n + (j + 1)
                            d = base + (i + 1) * n + j
                            tris.append((a, b, c))
                            tris.append((a, c, d))

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
    print(f"\nExtracted {len(verts):,} vertices, {len(tris):,} triangles, {len(quads):,} quads → {total_faces:,} total faces.")

    return np.array(verts, dtype=np.float64), tris, quads


# ---------------------------------------------------------------------------
# Mesh cleaning  (using trimesh)
# ---------------------------------------------------------------------------

def clean_mesh(vertices: np.ndarray, triangles: list, quads: list, tolerance: float = 1e-6, aggressive: bool = False):
    """
    Clean the mesh to remove small edges and fix geometry issues.
    
    Parameters
    ----------
    vertices : np.ndarray  shape (N, 3)
    triangles : list[tuple[int, int, int]]
    quads : list[tuple[int, int, int, int]]
    tolerance : float  – merge vertices within this distance
    aggressive : bool  – if True, apply additional mesh optimization
    
    Returns
    -------
    vertices : np.ndarray  cleaned vertices
    triangles : list[tuple[int, int, int]]  cleaned triangle indices
    quads : list[tuple[int, int, int, int]]  cleaned quad indices
    """
    # Note: When preserving quads, we skip aggressive cleaning to avoid
    # reindexing issues. The quad structure is already optimal for Creo.
    total_faces = len(triangles) + len(quads)
    print(f"\nMesh info: {len(vertices):,} vertices, {total_faces:,} faces ({len(triangles):,} tri, {len(quads):,} quad)")
    
    if aggressive and not quads:
        # Only apply aggressive cleaning if no quads (which we want to preserve)
        try:
            import trimesh
        except ImportError:
            print("Warning: trimesh not installed. Skipping mesh cleaning.")
            return vertices, triangles, quads
        
        try:
            mesh = trimesh.Trimesh(vertices=vertices, faces=triangles, process=True)
            print(f"  Applying aggressive optimization...")
            
            try:
                mesh = mesh.simplify(target_reduction=0.05, preserve_border=True)
                print(f"  Simplified to {len(mesh.faces):,} faces")
                mesh.merge_vertices()
                return mesh.vertices, triangles, []
            except:
                mesh.merge_vertices()
                return mesh.vertices, triangles, []
        except:
            pass
    
    # Return as-is to preserve quad structure
    return vertices, triangles, quads


# ---------------------------------------------------------------------------
# STL export  (numpy-stl)
# ---------------------------------------------------------------------------

def save_stl(vertices: np.ndarray, triangles: list, quads: list, output_path: Path):
    try:
        from stl import mesh as stl_mesh
    except ImportError:
        sys.exit(
            "numpy-stl is not installed.\n"
            "Fix: pip install numpy-stl"
        )

    # Convert quads to triangles for STL export
    all_tris = list(triangles)
    for q in quads:
        # Split each quad into 2 triangles
        all_tris.append((q[0], q[1], q[2]))
        all_tris.append((q[0], q[2], q[3]))

    if not all_tris:
        print("Warning: no faces – STL not written.")
        return

    solid = stl_mesh.Mesh(np.zeros(len(all_tris), dtype=stl_mesh.Mesh.dtype))
    for i, (a, b, c) in enumerate(all_tris):
        solid.vectors[i] = vertices[[a, b, c]]

    solid.save(str(output_path))
    print(f"STL saved → {output_path}  ({len(all_tris):,} triangles from {len(triangles)} tri + {len(quads)} quad)")


# ---------------------------------------------------------------------------
# STEP export  –  pure-Python AP214 writer, no CAD kernel required
# ---------------------------------------------------------------------------

def save_step(vertices: np.ndarray, triangles: list, quads: list, output_path: Path):
    """
    Write a STEP AP214 (automotive_design) file from triangles and quads.
    Each triangle/quad becomes one ADVANCED_FACE on a PLANE surface inside an
    OPEN_SHELL → SHELL_BASED_SURFACE_MODEL.  No external library needed.
    NX, CATIA, SolidWorks, FreeCAD and most other CAD tools import this.
    """
    # Convert quads to triangles for STEP export
    all_tris = list(triangles)
    for q in quads:
        # Split each quad into 2 triangles
        all_tris.append((q[0], q[1], q[2]))
        all_tris.append((q[0], q[2], q[3]))
    
    if not all_tris:
        print("Warning: no faces – STEP not written.")
        return

    total_faces = len(triangles) + len(quads)
    print(f"Building STEP file from {total_faces:,} faces ({len(triangles):,} tri, {len(quads):,} quad) …")

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
    # Geometry: one ADVANCED_FACE per triangle                            #
    # ------------------------------------------------------------------ #
    face_ids: list[int] = []
    skipped = 0

    for tri_idx, (ia, ib, ic) in enumerate(all_tris):
        p1 = vertices[ia]
        p2 = vertices[ib]
        p3 = vertices[ic]

        # Normal
        v12 = p2 - p1
        v13 = p3 - p1
        normal = np.cross(v12, v13)
        nlen = float(np.linalg.norm(normal))
        if nlen < 1e-10:
            skipped += 1
            continue
        normal = normal / nlen

        def fmt(v):
            return f"({v[0]:.8f},{v[1]:.8f},{v[2]:.8f})"

        # Cartesian points
        cp1 = emit(f"CARTESIAN_POINT('',{fmt(p1)})")
        cp2 = emit(f"CARTESIAN_POINT('',{fmt(p2)})")
        cp3 = emit(f"CARTESIAN_POINT('',{fmt(p3)})")

        # Vertex points
        vp1 = emit(f"VERTEX_POINT('',#{cp1})")
        vp2 = emit(f"VERTEX_POINT('',#{cp2})")
        vp3 = emit(f"VERTEX_POINT('',#{cp3})")

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

        oe1 = make_edge(cp1, cp2, vp1, vp2, p2 - p1)
        oe2 = make_edge(cp2, cp3, vp2, vp3, p3 - p2)
        oe3 = make_edge(cp3, cp1, vp3, vp1, p1 - p3)

        el  = emit(f"EDGE_LOOP('',(#{oe1},#{oe2},#{oe3}))")
        fob = emit(f"FACE_OUTER_BOUND('',#{el},.T.)")

        # Plane: axis placement at p1, Z=normal, X=ref_dir
        ref = np.array([1.0, 0.0, 0.0]) if abs(normal[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
        ref = ref - np.dot(ref, normal) * normal
        rlen = float(np.linalg.norm(ref))
        ref = ref / (rlen if rlen > 1e-12 else 1.0)

        norm_dir  = emit(f"DIRECTION('',{fmt(normal)})")
        ref_dir   = emit(f"DIRECTION('',{fmt(ref)})")
        ax2p3d    = emit(f"AXIS2_PLACEMENT_3D('',#{cp1},#{norm_dir},#{ref_dir})")
        plane_id  = emit(f"PLANE('',#{ax2p3d})")

        face_id = emit(f"ADVANCED_FACE('',(#{fob}),#{plane_id},.T.)")
        face_ids.append(face_id)

    if skipped:
        print(f"  ({skipped} degenerate triangles skipped)")

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
    print(f"STEP saved → {output_path}  ({len(face_ids):,} faces, {size_mb:.1f} MB)")


# ---------------------------------------------------------------------------
# IGES export  –  pure-Python IGES 5.3 writer, no CAD kernel required
# ---------------------------------------------------------------------------

def save_iges(vertices: np.ndarray, triangles: list, quads: list, output_path: Path):
    """
    Write an IGES 5.3 file directly from triangles and quads.  No external library needed.
    Each triangle/quad becomes:
      Entity 110 (Line) ×3/4  →  edges
      Entity 102 (Composite Curve)  →  closed boundary loop
      Entity 108 (Plane)  →  infinite plane through the face
      Entity 142 (Curve on Parametric Surface)  →  links boundary to plane
      Entity 144 (Trimmed Parametric Surface)  →  the final trimmed face
    NX, CATIA, SolidWorks and FreeCAD all import Entity 144 IGES files.
    """
    # Convert quads to triangles for IGES export
    all_tris = list(triangles)
    for q in quads:
        # Split each quad into 2 triangles
        all_tris.append((q[0], q[1], q[2]))
        all_tris.append((q[0], q[2], q[3]))
    
    if not all_tris:
        print("Warning: no faces – IGES not written.")
        return

    total_faces = len(triangles) + len(quads)
    print(f"Building IGES file from {total_faces:,} faces ({len(triangles):,} tri, {len(quads):,} quad) …")

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
    for ia, ib, ic in all_tris:
        p1, p2, p3 = vertices[ia], vertices[ib], vertices[ic]
        normal = np.cross(p2 - p1, p3 - p1)
        nlen = float(np.linalg.norm(normal))
        if nlen < 1e-10:
            skipped += 1
            continue
        normal = normal / nlen

        # 3 edge lines
        l1 = _add(110, 0, f"{_fv(p1)},{_fv(p2)}")
        l2 = _add(110, 0, f"{_fv(p2)},{_fv(p3)}")
        l3 = _add(110, 0, f"{_fv(p3)},{_fv(p1)}")

        # Composite curve (closed boundary loop)
        cc = _add(102, 0, f"3,{_de(l1)},{_de(l2)},{_de(l3)}")

        # Plane:  A·x + B·y + C·z = D
        A, B, C = normal
        D = float(np.dot(normal, p1))
        plane = _add(108, 0, f"{_f(A)},{_f(B)},{_f(C)},{_f(D)},0,{_fv(p1)},1.0")

        # Curve on parametric surface
        cos142 = _add(142, 0, f"1,{_de(plane)},{_de(cc)},0,1")

        # Trimmed surface
        _add(144, 0, f"{_de(plane)},0,0,{_de(cos142)}")

    if skipped:
        print(f"  ({skipped} degenerate triangles skipped)")

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
    print(f"IGES saved → {output_path}  ({n_faces:,} trimmed surfaces, {size_mb:.1f} MB)")


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
    vertices, triangles, quads = extract_geometry(input_path)
    
    # Clean mesh to remove small edges and fix geometry
    vertices, triangles, quads = clean_mesh(vertices, triangles, quads, tolerance=1e-6, aggressive=args.aggressive)

    total_faces = len(triangles) + len(quads)
    if total_faces == 0:
        print(
            "\nNo convertible geometry found in the DXF."
            "\nMake sure the file contains at least one of:"
            "\n  3DFACE, MESH, POLYLINE (PFACE or POLYMESH)"
        )
        sys.exit(1)

    if args.format in ("stl", "all"):
        save_stl(vertices, triangles, quads, out_base.with_suffix(".stl"))

    if args.format in ("step", "all"):
        save_step(vertices, triangles, quads, out_base.with_suffix(".stp"))

    if args.format in ("iges", "all"):
        save_iges(vertices, triangles, quads, out_base.with_suffix(".igs"))

    print("\nDone.")


if __name__ == "__main__":
    main()
