// The 3Dmol.js viewer as a NiceGUI custom element (Phase 16, Stage 16.5).
//
// The scene code is ui/components/mol_viewer/index.html's (Streamlit's
// viewer), minus its postMessage layer. Unlike Streamlit, one viewer lives
// as long as the element: the scene (receptor, ligands, mode) is built once
// in mounted(), and only setBox / setLigands redraw parts of it, so the
// camera the user set is kept. Python side: ui_nice/mol_viewer.py.

function loadScript(url) {
  window.__molViewerLoads = window.__molViewerLoads || {};
  if (!window.__molViewerLoads[url]) {
    window.__molViewerLoads[url] = new Promise((resolve, reject) => {
      const el = document.createElement("script");
      el.src = url;
      el.onload = resolve;
      el.onerror = reject;
      document.head.appendChild(el);
    });
  }
  return window.__molViewerLoads[url];
}

function affinityColorHex(affinity, minAff, maxAff) {
  if (affinity === null || affinity === undefined) return "0x808080";
  const ratio = maxAff !== minAff ? (affinity - minAff) / (maxAff - minAff) : 0.5;
  let r, g;
  if (ratio < 0.5) { r = ratio * 2; g = 1.0; } else { r = 1.0; g = 2.0 * (1.0 - ratio); }
  const toHex = (v) => {
    const h = Math.round(v * 255).toString(16);
    return h.length < 2 ? "0" + h : h;
  };
  return "0x" + toHex(r) + toHex(g) + "00";
}

// ---- ?perf timing (Stage 16.0 baseline; docs/GUI_GUIDE.md "Performance") --
// Off unless the page URL has ?perf. Same console line as Streamlit's
// viewer, so benchmark_gui_browser.py parses both; mode is box_picker /
// view for a scene build, box for setBox, ligands for setLigands.
const perfEnabled = (() => {
  try {
    return new URLSearchParams(window.location.search).has("perf");
  } catch (e) {
    return false;
  }
})();
window.__viewerPerf = window.__viewerPerf || [];
let perfRenderCount = 0;

function perfStart() {
  if (!perfEnabled) return null;
  perfRenderCount += 1;
  return { n: perfRenderCount, t0: performance.now() };
}

function perfEnd(viewer, token, mode) {
  if (!token) return;
  const renderMs = performance.now() - token.t0;
  const atoms = viewer.selectedAtoms({}).length;
  requestAnimationFrame(() => {
    const entry = {
      render: token.n, mode: mode, atoms: atoms,
      renderMs: +renderMs.toFixed(1),
      frameMs: +(performance.now() - token.t0).toFixed(1),
    };
    window.__viewerPerf.push(entry);
    console.info("[perf] render #" + entry.render + " mode=" + mode + " atoms=" + atoms +
      " render=" + entry.renderMs + "ms frame=" + entry.frameMs + "ms");
  });
}

// Box outline as 12 opaque cylinder edges instead of a filled translucent
// box: a transparent fill hides whatever is drawn after it (3Dmol doesn't
// depth-sort transparent objects), and wireframe lines are 1px in WebGL,
// too thin to see. Cylinders are always visible and never cover the protein.
const BOX_EDGES = [
  // 4 edges along X, then Y, then Z
  [[-1, -1, -1], [1, -1, -1]], [[-1, 1, -1], [1, 1, -1]],
  [[-1, -1, 1], [1, -1, 1]], [[-1, 1, 1], [1, 1, 1]],
  [[-1, -1, -1], [-1, 1, -1]], [[1, -1, -1], [1, 1, -1]],
  [[-1, -1, 1], [-1, 1, 1]], [[1, -1, 1], [1, 1, 1]],
  [[-1, -1, -1], [-1, -1, 1]], [[1, -1, -1], [1, -1, 1]],
  [[-1, 1, -1], [-1, 1, 1]], [[1, 1, -1], [1, 1, 1]],
];

const AXES = [
  { dir: [1, 0, 0], color: "red", label: "X" },
  { dir: [0, 1, 0], color: "green", label: "Y" },
  { dir: [0, 0, 1], color: "blue", label: "Z" },
];

export default {
  template: `
    <div style="position:relative;width:100%;height:100%;min-height:300px">
      <div ref="hint" style="position:absolute;top:4px;left:8px;z-index:10;font:12px sans-serif;color:#444;
        background:rgba(255,255,255,0.75);padding:2px 6px;border-radius:3px;pointer-events:none"></div>
      <div ref="canvas" style="position:absolute;inset:0"></div>
    </div>`,
  props: {
    libraryUrl: String,
    mode: String,
    receptorPdb: String,
    ligands: Object,
    center: Array,
    boxSize: Array,
  },
  async mounted() {
    await this.$nextTick();
    await loadScript(window.path_prefix + this.libraryUrl);
    const $3Dmol = window["3Dmol"] || window.$3Dmol;
    this.viewer = $3Dmol.createViewer(this.$refs.canvas, { backgroundColor: "white" });
    this.buildScene();
    // ui.splitter resizes the container, not the window; a hidden panel
    // shown again has a size again. Follow both.
    this.resizeObserver = new ResizeObserver(() => {
      if (this.viewer) { this.viewer.resize(); this.viewer.render(); }
    });
    this.resizeObserver.observe(this.$el);
  },
  unmounted() {
    if (this.resizeObserver) this.resizeObserver.disconnect();
  },
  methods: {
    // The whole scene, once: the only place the camera is zoomed.
    buildScene() {
      const viewer = this.viewer;
      const perfToken = perfStart();
      const mode = this.mode || "view";
      this.receptorModel = null;
      this.ligandModels = [];
      this.axisLabels = null;
      this.atomLabel = null;
      if (this.receptorPdb) {
        this.receptorModel = viewer.addModel(this.receptorPdb, "pdb");
        this.drawLigands(this.latestLigands || this.ligands || {});
        if (mode === "box_picker") {
          const [center, size] = this.latestBox || [this.center, this.boxSize];
          this.drawBox(center, size);
          this.$refs.hint.textContent = "Click a receptor atom to set the docking box center";
          viewer.setClickable({ model: this.receptorModel }, true, (atom) => this.onAtomClick(atom));
        }
      }
      viewer.zoomTo();
      viewer.render();
      perfEnd(viewer, perfToken, mode);
    },
    // Receptor style: the base cartoon, plus the residues around the first
    // ligand (results) or inside the box (box picker).
    styleReceptor() {
      const receptorModel = this.receptorModel;
      // Box picker: keep the cartoon fully opaque. A translucent cartoon
      // joins 3Dmol's unsorted transparent pass and gets hidden behind (or
      // inside) any other transparent shape drawn first.
      this.viewer.setStyle({ model: receptorModel }, {
        cartoon: { color: "gray", opacity: this.mode === "box_picker" ? 1.0 : 0.85 },
      });
      if (this.ligandModels.length) {
        this.viewer.setStyle(
          { within: { distance: 5, sel: { model: this.ligandModels[0] } }, model: receptorModel },
          { stick: { radius: 0.15, colorscheme: "grayCarbon" } }
        );
      }
    },
    // Ligand poses: {ligand_id: {sdf: str, affinity: number|null}}
    drawLigands(ligands) {
      const viewer = this.viewer;
      this.ligandModels.forEach((m) => viewer.removeModel(m));
      this.ligandModels = [];
      const ligandIds = Object.keys(ligands);
      const affinities = ligandIds
        .map((id) => ligands[id].affinity)
        .filter((a) => a !== null && a !== undefined);
      const minAff = affinities.length ? Math.min.apply(null, affinities) : 0;
      const maxAff = affinities.length ? Math.max.apply(null, affinities) : 0;
      ligandIds.forEach((ligandId) => {
        const entry = ligands[ligandId];
        if (!entry || !entry.sdf) return;
        const ligandModel = viewer.addModel(entry.sdf, "sdf");
        this.ligandModels.push(ligandModel);
        const color = affinities.length ? affinityColorHex(entry.affinity, minAff, maxAff) : "orange";
        viewer.setStyle({ model: ligandModel }, { stick: { radius: 0.2, color: color } });
      });
      this.styleReceptor();
    },
    drawBox(center, boxSize) {
      const viewer = this.viewer;
      viewer.removeAllShapes();
      this.styleReceptor();
      if (!center) {
        this.dropLabels();
        return;
      }
      boxSize = boxSize || [20, 20, 20];
      const hx = boxSize[0] / 2, hy = boxSize[1] / 2, hz = boxSize[2] / 2;

      // Residues with any atom inside the box: orange cartoon + thin
      // sticks, so the region that will actually be docked into stands out.
      const insideBox = (atom) =>
        Math.abs(atom.x - center[0]) <= hx &&
        Math.abs(atom.y - center[1]) <= hy &&
        Math.abs(atom.z - center[2]) <= hz;
      viewer.setStyle(
        { model: this.receptorModel, predicate: insideBox, byres: true },
        { cartoon: { color: "orange" }, stick: { radius: 0.12, colorscheme: "grayCarbon" } }
      );

      const corner = (sx, sy, sz) => ({ x: center[0] + sx * hx, y: center[1] + sy * hy, z: center[2] + sz * hz });
      BOX_EDGES.forEach((e) => {
        viewer.addCylinder({
          start: corner(e[0][0], e[0][1], e[0][2]),
          end: corner(e[1][0], e[1][1], e[1][2]),
          radius: 0.15, color: "#1f4fd8", fromCap: 1, toCap: 1,
        });
      });
      viewer.addSphere({ center: { x: center[0], y: center[1], z: center[2] }, radius: 0.5, color: "red" });

      // RGB axis arrows (X=red, Y=green, Z=blue -- the standard 3D-tool
      // convention) from the box center out past each face, so it's clear
      // which box edge the corresponding Box size X/Y/Z field controls.
      const ends = AXES.map((a, i) => {
        const len = [hx, hy, hz][i] + 2;
        const end = { x: center[0] + a.dir[0] * len, y: center[1] + a.dir[1] * len, z: center[2] + a.dir[2] * len };
        viewer.addArrow({
          start: { x: center[0], y: center[1], z: center[2] }, end: end, radius: 0.3, color: a.color, mid: 0.8,
        });
        return end;
      });
      this.placeAxisLabels(ends);
      this.placeAtomLabel(center);
    },
    // Labels are the expensive part of a redraw: 3Dmol draws each one's
    // text into a new texture, and removeLabel / removeAllLabels re-render
    // the whole scene. So the axis labels are made once (noshow: no render)
    // and then only moved.
    placeAxisLabels(ends) {
      if (!this.axisLabels) {
        this.axisLabels = AXES.map((a, i) => this.viewer.addLabel(
          a.label, { position: ends[i], backgroundColor: a.color, fontColor: "white", fontSize: 14 }, undefined, true
        ));
        return;
      }
      this.axisLabels.forEach((label, i) => {
        const end = ends[i];
        label.stylespec.position = end;  // kept if 3Dmol redraws the label
        label.sprite.position.set(end.x, end.y, end.z);
      });
    },
    dropLabels() {
      (this.axisLabels || []).concat(this.atomLabel ? [this.atomLabel] : []).forEach((l) => this.viewer.removeLabel(l));
      this.axisLabels = null;
      this.atomLabel = null;
    },
    // The clicked atom's name, while the box is still centred on it
    // (Python writes the center rounded to 3 decimals).
    placeAtomLabel(center) {
      const atom = this.clickedAtom;
      const centred = atom && [atom.x, atom.y, atom.z].every((v, i) => Math.abs(v - center[i]) < 0.001);
      if (this.atomLabel && (!centred || this.atomLabelFor !== atom)) {
        this.viewer.removeLabel(this.atomLabel);
        this.atomLabel = null;
      }
      if (centred && !this.atomLabel) {
        this.atomLabel = this.viewer.addLabel(
          (atom.resn || "") + " " + (atom.resi !== undefined ? atom.resi : "") + " / " + atom.atom,
          { position: { x: atom.x, y: atom.y, z: atom.z }, backgroundColor: "black", fontColor: "white" },
          undefined, true
        );
        this.atomLabelFor = atom;
      }
    },
    onAtomClick(atom) {
      this.clickedAtom = { x: atom.x, y: atom.y, z: atom.z, resn: atom.resn, resi: atom.resi, atom: atom.atom };
      // Python writes the center into the Docking Box fields and calls
      // setBox back, which redraws the box with this atom's label.
      this.$emit("atom_click", { center: [atom.x, atom.y, atom.z] });
    },
    // Called from Python (MolViewer.set_box): redraw only the box overlay.
    setBox(center, boxSize) {
      this.latestBox = [center, boxSize];
      if (!this.viewer || !this.receptorModel) return;  // mounted() applies latestBox
      const perfToken = perfStart();
      this.drawBox(center, boxSize);
      this.viewer.render();
      perfEnd(this.viewer, perfToken, "box");
    },
    // Called from Python (MolViewer.set_ligands): swap only the poses.
    setLigands(ligands) {
      this.latestLigands = ligands;
      if (!this.viewer || !this.receptorModel) return;  // mounted() applies latestLigands
      const perfToken = perfStart();
      this.drawLigands(ligands || {});
      if (this.mode === "box_picker") {
        const [center, size] = this.latestBox || [this.center, this.boxSize];
        this.drawBox(center, size);
      }
      this.viewer.render();
      perfEnd(this.viewer, perfToken, "ligands");
    },
    // For checks in a browser: the camera, to compare before and after.
    getView() {
      return this.viewer ? this.viewer.getView() : null;
    },
  },
};
