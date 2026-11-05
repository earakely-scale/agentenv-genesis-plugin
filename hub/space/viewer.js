// The workbench's 3D view: the object's Genesis visual meshes (one GLB node per geom, named v0, v1, ...) placed at
// Genesis poses, on a real-scale grid beside a 330 ml can. It replays what Genesis recorded during an action: every
// frame gives each geom's [x, y, z, qw, qx, qy, qz] in Genesis's z-up frame, which this view uses as is.
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";

THREE.Object3D.DEFAULT_UP.set(0, 0, 1);
const CAN = { r: 0.033, h: 0.122 };
const quat = (p) => new THREE.Quaternion(p[4], p[5], p[6], p[3]);

function gridTexture(cells, dark) {
  const c = document.createElement("canvas");
  c.width = c.height = 512;
  const g = c.getContext("2d");
  g.fillStyle = dark ? "#2a3136" : "#e9ebe8";
  g.fillRect(0, 0, 512, 512);
  const step = 512 / cells;
  for (let i = 0; i <= cells; i++) {
    g.strokeStyle = i % 5 === 0 ? (dark ? "#55616a" : "#b9bfbb") : (dark ? "#39424a" : "#d6dad6");
    g.lineWidth = i % 5 === 0 ? 3 : 1.5;
    g.beginPath(); g.moveTo(i * step, 0); g.lineTo(i * step, 512); g.stroke();
    g.beginPath(); g.moveTo(0, i * step); g.lineTo(512, i * step); g.stroke();
  }
  const t = new THREE.CanvasTexture(c);
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  t.anisotropy = 8;
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

export class SimViewer {
  constructor(el, { dark = false, onFrame = null } = {}) {
    this.el = el;
    this.onFrame = onFrame;
    this.renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    el.appendChild(this.renderer.domElement);
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(dark ? 0x12171a : 0xdfe4e1);
    this.scene.environment = new THREE.PMREMGenerator(this.renderer).fromScene(new RoomEnvironment(), 0.04).texture;
    this.scene.environmentIntensity = 0.55;
    this.world = new THREE.Group();          // tilts for the lean test; the camera stays put
    this.scene.add(this.world);
    this.camera = new THREE.PerspectiveCamera(35, 1, 0.005, 200);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.scene.add(new THREE.HemisphereLight(0xffffff, 0x8a8f8c, 1.1));
    this.sun = new THREE.DirectionalLight(0xffffff, 2.4);
    this.sun.castShadow = true;
    this.sun.shadow.mapSize.set(2048, 2048);
    this.sun.shadow.bias = -0.0004;
    this.scene.add(this.sun, this.sun.target);
    this.dark = dark;
    this.nodes = [];
    this.balls = [];
    this.trace = null;
    this.playing = false;
    this.speed = 1;
    this.t = 0;
    new ResizeObserver(() => this.resize()).observe(el);
    this.resize();
    const tick = (now) => {
      requestAnimationFrame(tick);
      if (this.playing && this.trace) {
        const dt = Math.min(0.05, (now - (this.last || now)) / 1000);
        this.seek(this.t + dt * this.speed, false);
        if (this.t >= this.duration()) { this.playing = false; this.onFrame && this.onFrame(this.status()); }
      }
      this.last = now;
      this.controls.update();
      this.renderer.render(this.scene, this.camera);
    };
    requestAnimationFrame(tick);
  }

  resize() {
    const w = this.el.clientWidth, h = this.el.clientHeight || w * 0.75;
    this.renderer.setSize(w, h, false);
    this.renderer.domElement.style.width = "100%";
    this.renderer.domElement.style.height = "100%";
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  async load(glbUrl, meta, ghost = null) {
    const loader = new GLTFLoader();
    const gltf = await loader.loadAsync(glbUrl);
    if (this.object) this.world.remove(this.object);
    this.object = new THREE.Group();
    this.meta = meta;
    this.nodes = meta.geoms.map((g) => {
      const n = gltf.scene.getObjectByName(`v${g.i}`) || new THREE.Group();
      n.traverse((m) => {
        if (!m.isMesh) return;
        // glTF's default is fully metallic; these exports carry colours or textures, not metalness.
        if (m.material.metalness !== undefined) { m.material.metalness = 0; m.material.roughness = Math.min(m.material.roughness ?? 0.6, 0.7); }
        m.castShadow = m.receiveShadow = true;
        m.userData.base = m.material;
      });
      this.object.add(n);
      return n;
    });
    this.rest = meta.geoms.map((g) => g.pose);
    this.setPose(this.rest);
    this.world.add(this.object);
    this.frame(meta.bounds);
    this.setCom(this.comOn ?? false);
    if (ghost) await this.loadGhost(ghost.url, ghost.meta);
    return this;
  }

  async loadGhost(url, meta) {
    const gltf = await new GLTFLoader().loadAsync(url);
    if (this.ghost) this.world.remove(this.ghost);
    this.ghost = new THREE.Group();
    const mat = new THREE.MeshBasicMaterial({ color: 0x3cc2b2, transparent: true, opacity: 0.22, depthWrite: false });
    const edge = new THREE.LineBasicMaterial({ color: 0x0b7a72, transparent: true, opacity: 0.55 });
    for (const g of meta.geoms) {
      const n = gltf.scene.getObjectByName(`v${g.i}`);
      if (!n) continue;
      n.traverse((m) => {
        if (!m.isMesh) return;
        m.material = mat;
        m.add(new THREE.LineSegments(new THREE.EdgesGeometry(m.geometry, 28), edge));
      });
      n.position.set(g.pose[0], g.pose[1], g.pose[2]);
      n.quaternion.copy(quat(g.pose));
      this.ghost.add(n);
    }
    this.ghost.visible = this.ghostOn ?? false;
    this.world.add(this.ghost);
  }

  setGhost(on) { this.ghostOn = on; if (this.ghost) this.ghost.visible = on; }

  setCom(on) {
    this.comOn = on;
    if (this.com) { this.com.parent && this.com.parent.remove(this.com); this.com = null; }
    if (!on || !this.meta || !this.meta.com || !this.nodes[0]) return;
    const p0 = this.rest[0], local = new THREE.Vector3(...this.meta.com).sub(new THREE.Vector3(p0[0], p0[1], p0[2]))
      .applyQuaternion(quat(p0).invert());
    const s = Math.max(0.006, this.size * 0.035);
    this.com = new THREE.Group();
    const q = [0xffffff, 0x14191b];
    for (let k = 0; k < 8; k++) {
      const geo = new THREE.SphereGeometry(s, 12, 8, (k % 4) * Math.PI / 2, Math.PI / 2, k < 4 ? 0 : Math.PI / 2, Math.PI / 2);
      this.com.add(new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ color: q[(k + (k < 4 ? 0 : 1)) % 2], depthTest: false })));
    }
    this.com.renderOrder = 10;
    this.com.children.forEach((c) => (c.renderOrder = 10));
    this.com.position.copy(local);
    this.nodes[0].add(this.com);
  }

  placeCan(bounds) {
    const cx = bounds[1][0] + 0.04 + CAN.r;
    if (!this.can) {
      this.can = new THREE.Mesh(new THREE.CylinderGeometry(CAN.r, CAN.r, CAN.h, 48),
        new THREE.MeshStandardMaterial({ color: 0xc72a1e, roughness: 0.35, metalness: 0.3 }));
      this.can.rotation.x = Math.PI / 2;
      this.can.castShadow = this.can.receiveShadow = true;
      this.world.add(this.can);
    }
    this.can.position.set(cx, 0, CAN.h / 2);
    return cx;
  }

  // The object at a point in a session, drawn from the as-delivered meshes: uniformly rescaled (set_scale is relative
  // to the delivered geometry) and with removed parts hidden.
  setState({ scale = 1, removed = [] } = {}) {
    if (!this.meta) return;
    const k = scale / (this.meta.scale || 1), z0 = this.meta.lift || 0;
    this.meta.geoms.forEach((g, i) => {
      this.nodes[i].scale.setScalar(k);
      this.nodes[i].visible = !(g.part && removed.includes(g.part));
    });
    this.rest = this.meta.geoms.map(({ pose: p }) => [p[0] * k, p[1] * k, z0 + (p[2] - z0) * k, p[3], p[4], p[5], p[6]]);
    const b = this.meta.bounds;
    this.placeCan([[b[0][0] * k, b[0][1] * k, b[0][2]], [b[1][0] * k, b[1][1] * k, b[1][2] * k]]);
    if (!this.trace) this.setPose(this.rest);
  }

  frame(bounds) {
    const lo = new THREE.Vector3(...bounds[0]), hi = new THREE.Vector3(...bounds[1]);
    const size = Math.max(hi.x - lo.x, hi.y - lo.y, hi.z - lo.z);
    this.size = size;
    const small = size < 0.6;
    const cx = this.placeCan(bounds);
    // Grid: 1 cm cells with a darker line every 5 cm for small objects; 10 cm and 50 cm for furniture.
    const cell = small ? 0.01 : 0.1, span = small ? 1.0 : 10;
    if (this.ground) this.world.remove(this.ground);
    const tex = gridTexture(50, this.dark);
    tex.repeat.set(span / (cell * 50), span / (cell * 50));
    this.ground = new THREE.Mesh(new THREE.PlaneGeometry(span, span), new THREE.MeshStandardMaterial({ map: tex, roughness: 0.95 }));
    this.ground.receiveShadow = true;
    this.world.add(this.ground);
    this.gridLabel = small ? "grid: 1 cm squares, 5 cm lines" : "grid: 10 cm squares, 50 cm lines";
    const ctr = new THREE.Vector3((lo.x + Math.max(hi.x, cx + CAN.r)) / 2, (lo.y + hi.y) / 2, (hi.z - lo.z) * 0.38);
    const d = Math.max(size, CAN.h) * (small ? 2.4 : 1.25) + 0.05;
    this.camera.position.set(ctr.x + d * 0.62, ctr.y - d * 0.92, ctr.z + d * 0.62);
    this.controls.target.copy(ctr);
    this.controls.minDistance = size * 0.4;
    this.controls.maxDistance = d * 6;
    this.camera.near = Math.max(0.002, d / 500);
    this.camera.updateProjectionMatrix();
    const L = d * 1.4;
    Object.assign(this.sun.shadow.camera, { left: -L, right: L, top: L, bottom: -L, near: 0.01, far: L * 6 });
    this.sun.shadow.camera.updateProjectionMatrix();
    this.sun.position.set(ctr.x - L * 0.6, ctr.y - L * 0.9, L * 2.2);
    this.sun.target.position.copy(ctr);
  }

  setPose(frame) {
    frame.forEach((p, i) => {
      const n = this.nodes[i];
      if (!n) return;
      n.position.set(p[0], p[1], p[2]);
      n.quaternion.copy(quat(p));
    });
  }

  highlight(parts) {
    if (!this.meta) return;
    this.meta.geoms.forEach((g, i) => {
      this.nodes[i].traverse((m) => {
        if (!m.isMesh) return;
        if (parts && parts.includes(g.part)) {
          m.material = m.userData.base.clone();
          m.material.color = new THREE.Color(0xe0321f);
          m.material.map = null;
          m.material.vertexColors = false;
          m.material.emissive = new THREE.Color(0x9a1a10);
          m.material.emissiveIntensity = 0.6;
        } else m.material = m.userData.base;
      });
    });
  }

  // Replay one action. tip: the table tilts by lean × ramp while the object moves; cavity: marbles fall in;
  // parts: loose parts light up; lift: the object rises and comes down with its weight shown.
  play(trace) {
    this.trace = trace;
    this.highlight(trace.kind === "parts" ? trace.floating : null);
    for (const b of this.balls) this.world.remove(b);
    this.balls = (trace.balls ? trace.balls[0] : []).map(() => {
      const m = new THREE.Mesh(new THREE.SphereGeometry(0.008, 20, 14), new THREE.MeshStandardMaterial({ color: 0xe8b13a, metalness: 0.2, roughness: 0.3 }));
      m.castShadow = true;
      this.world.add(m);
      return m;
    });
    if (this.can) this.can.visible = trace.kind !== "tip";
    this.t = 0;
    this.playing = !!(trace.frames && trace.frames.length) || trace.kind === "lift";
    this.seek(0, true);
  }

  duration() {
    const tr = this.trace;
    if (!tr) return 0;
    if (tr.kind === "lift") return 2.4;
    return tr.t && tr.t.length ? tr.t[tr.t.length - 1] : 0;
  }

  seek(t, notify = true) {
    const tr = this.trace;
    if (!tr) return;
    this.t = Math.max(0, Math.min(t, this.duration()));
    if (tr.kind === "lift") {
      const u = this.t / 2.4, h = Math.sin(Math.min(1, u * 1.6) * Math.PI) * 0.6 + (u > 0.3 && u < 0.7 ? 0.4 : 0);
      const lift = Math.min(1, h) * Math.max(0.05, this.size * 0.6);
      this.setPose(this.rest.map((p) => [p[0], p[1], p[2] + lift, p[3], p[4], p[5], p[6]]));
    } else if (tr.frames && tr.frames.length) {
      let i = 0;
      while (i < tr.t.length - 1 && tr.t[i + 1] <= this.t) i++;
      this.setPose(tr.frames[i]);
      if (tr.kind === "tip") {
        const a = THREE.MathUtils.degToRad(tr.lean_deg * (tr.ramp ? tr.ramp[i] : 1));
        // gravity tilted toward +x turns the table about y; toward +y (a set leaning back), about x
        if (tr.tilt_axis === "x") { this.world.rotation.set(-a, 0, 0); } else { this.world.rotation.set(0, a, 0); }
      }
      if (tr.balls) tr.balls[i].forEach((p, k) => this.balls[k] && this.balls[k].position.set(p[0], p[1], p[2]));
      this.index = i;
    }
    if (tr.kind !== "tip") this.world.rotation.set(0, 0, 0);
    if (this.onFrame) this.onFrame(this.status());
  }

  status() {
    const tr = this.trace;
    if (!tr) return null;
    const out = { t: this.t, duration: this.duration(), playing: this.playing, kind: tr.kind };
    if (tr.kind === "tip" && tr.ramp) out.label = `table tilted ${(tr.lean_deg * tr.ramp[this.index || 0]).toFixed(0)}°`;
    if (tr.kind === "joints" && tr.joint) out.label = `opening ${tr.joint[this.index || 0]}`;
    if (tr.kind === "drop") out.label = `t = ${this.t.toFixed(2)} s after release`;
    if (tr.kind === "cavity") out.label = "five 8 mm marbles dropped in";
    if (tr.kind === "parts") out.label = tr.floating && tr.floating.length ? `loose: ${tr.floating.join(", ")}` : "no loose parts";
    if (tr.kind === "seat") out.label = "every knife dropped into its slot from 2 cm above";
    if (tr.kind === "extract") { const n = tr.pulling && tr.pulling[this.index || 0]; out.label = n ? `pulling ${n.replace(/_/g, " ")} out` : "letting go"; }
    if (tr.kind === "rest") out.label = "settling from the designed poses";
    if (tr.engine) out.engine = tr.engine;
    return out;
  }

  reset() {
    if (this.can) this.can.visible = true;
    this.trace = null;
    this.playing = false;
    this.world.rotation.set(0, 0, 0);
    for (const b of this.balls) this.world.remove(b);
    this.balls = [];
    if (this.meta) { this.highlight(null); this.setPose(this.rest); }
    if (this.onFrame) this.onFrame(null);
  }

  snapshot() { return this.renderer.domElement.toDataURL("image/png"); }
}
