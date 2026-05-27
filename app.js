/**
 * DXF2CAD — runs dxf_converter.py in the browser via Pyodide (GitHub Pages).
 */

const PYODIDE_URL = "https://cdn.jsdelivr.net/pyodide/v0.26.4/full/";
const CONVERTER_PATH = "./dxf_converter.py";

const dxfInput = document.getElementById("dxfFile");
const formatSelect = document.getElementById("formatSelect");
const aggressiveInput = document.getElementById("aggressive");
const keepDegenerateInput = document.getElementById("keepDegenerate");
const stitchToleranceInput = document.getElementById("stitchTolerance");

const STITCH_MAP = {
  coarse: [1e-4, 1e-5, 1e-6],
  medium: [1e-5, 1e-6],
  fine:   [1e-6],
  tight:  [1e-7],
};
const convertBtn = document.getElementById("convertBtn");
const logEl = document.getElementById("log");
const downloadsEl = document.getElementById("downloads");

let pyodide = null;
let runtimeReady = false;
let runtimePromise = null;

function log(message) {
  logEl.textContent += `${message}\n`;
  logEl.scrollTop = logEl.scrollHeight;
}

function clearLog() {
  logEl.textContent = "";
}

function clearDownloads() {
  downloadsEl.innerHTML = "";
  downloadsEl.hidden = true;
}

function downloadBlob(filename, bytes) {
  const blob = new Blob([bytes], { type: "application/octet-stream" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.textContent = `Download ${filename}`;
  downloadsEl.appendChild(a);
  downloadsEl.hidden = false;
}

async function ensureRuntime() {
  if (runtimeReady) {
    return pyodide;
  }
  if (runtimePromise) {
    return runtimePromise;
  }

  runtimePromise = (async () => {
    log("Loading Python runtime (first visit may take ~30s)...");
    pyodide = await loadPyodide({ indexURL: PYODIDE_URL });

    await pyodide.loadPackage("micropip");
    log("Installing ezdxf (and dependencies)...");
    await pyodide.runPythonAsync(`
import micropip
await micropip.install("ezdxf")
`);

    const converterSource = await fetch(CONVERTER_PATH, { cache: "no-store" }).then((r) => {
      if (!r.ok) {
        throw new Error("Could not load dxf_converter.py from this site.");
      }
      return r.text();
    });
    pyodide.FS.writeFile("/dxf_converter.py", converterSource);

    log("Loading converter module...");
    await pyodide.runPythonAsync(`
import sys
if "/" not in sys.path:
    sys.path.insert(0, "/")
import importlib
import dxf_converter
importlib.reload(dxf_converter)
`);

    runtimeReady = true;
    log("Runtime ready.\n");
    return pyodide;
  })();

  return runtimePromise;
}

function stemFromFilename(name) {
  const base = name.replace(/\\/g, "/").split("/").pop() || "converted";
  const dot = base.lastIndexOf(".");
  return dot > 0 ? base.slice(0, dot) : base;
}

convertBtn.addEventListener("click", async () => {
  clearLog();
  clearDownloads();

  const file = dxfInput.files?.[0];
  if (!file) {
    log("Please upload a .dxf file.");
    return;
  }

  const fmt = formatSelect.value;
  const aggressive = aggressiveInput.checked;
  const keepDegenerate = keepDegenerateInput.checked;
  const stitchTolerances = STITCH_MAP[stitchToleranceInput.value] ?? STITCH_MAP.coarse;
  const stem = stemFromFilename(file.name);

  convertBtn.disabled = true;
  try {
    const py = await ensureRuntime();

    const bytes = new Uint8Array(await file.arrayBuffer());
    const inputPath = `/work/${file.name}`;
    const outBase = `/work/${stem}`;

    try {
      py.FS.mkdir("/work");
    } catch {
      /* already exists */
    }

    py.FS.writeFile(inputPath, bytes);

    py.globals.set("input_path", inputPath);
    py.globals.set("out_base", outBase);
    py.globals.set("fmt", fmt);
    py.globals.set("aggressive", aggressive);
    py.globals.set("keep_degenerate", keepDegenerate);
    py.globals.set("stitch_tolerances", py.toPy(stitchTolerances));

    log(`Converting ${file.name} → ${fmt}...`);

    await py.runPythonAsync(`
from pathlib import Path
import dxf_converter

result = dxf_converter.convert_dxf(
    Path(input_path),
    Path(out_base),
    fmt=fmt,
    aggressive=aggressive,
    keep_degenerate=keep_degenerate,
    stitch_tolerances=tuple(stitch_tolerances),
)
output_paths = [str(p) for p in result["outputs"]]
face_count = result["face_count"]
n_tri = len(result["triangles"])
n_quad = len(result["quads"])
n_verts = len(result["vertices"])
`);

    const faceCount = py.globals.get("face_count");
    const nTri = py.globals.get("n_tri");
    const nQuad = py.globals.get("n_quad");
    const nVerts = py.globals.get("n_verts");
    const outputPaths = py.globals.get("output_paths").toJs();

    log(
      `Done — ${faceCount.toLocaleString()} faces ` +
        `(${nTri.toLocaleString()} triangles, ${nQuad.toLocaleString()} quads), ` +
        `${nVerts.toLocaleString()} vertices.\n`
    );

    for (const path of outputPaths) {
      const name = path.split("/").pop();
      const data = py.FS.readFile(path, { encoding: "binary" });
      const sizeMb = (data.byteLength / (1024 * 1024)).toFixed(2);
      log(`Prepared ${name} (${sizeMb} MB)`);
      downloadBlob(name, data);
    }

    log("\nClick a download link above (or your browser may save automatically).");
  } catch (error) {
    const msg = error?.message || String(error);
    log(`Error: ${msg}`);
  } finally {
    convertBtn.disabled = false;
  }
});

// Warm up Pyodide in the background after the page loads.
window.addEventListener("load", () => {
  ensureRuntime().catch((error) => {
    log(`Runtime preload failed: ${error.message}`);
    log("Click Convert again after fixing your connection.");
  });
});
