from __future__ import annotations


FEW_SHOT_EXAMPLES = """\
## Worked examples — study the patterns, then write your own module

These two miniature exemplars demonstrate the idioms you should use for
common shapes. They are NOT a library to call — they are reference patterns.
Adapt them to whatever the reference image shows.

### Example 1 — Wooden chair (4 radial legs, seat, backrest)

Reference summary:
> A simple wooden chair with four straight cylindrical legs, a flat square
> seat, and a tall vertical-slat backrest. Walnut wood throughout.

```javascript
export default function generate(THREE) {
  // Materials — single shared wood material for every part keeps the
  // chair coherent and saves draw calls.
  const woodMat = new THREE.MeshStandardMaterial({
    color: 0x8b6f47,
    metalness: 0.0,
    roughness: 0.6,
  });

  const root = new THREE.Group();

  // Seat — flat square box, sits at mid-height.
  const seatGeom = new THREE.BoxGeometry(0.45, 0.04, 0.45);
  const seat = new THREE.Mesh(seatGeom, woodMat);
  seat.position.y = 0.40;
  root.add(seat);

  // Legs — 4 cylindrical legs, radial symmetric. Use one geometry +
  // four meshes so the model is robust to "wrong_count" critique.
  const legGeom = new THREE.CylinderGeometry(0.022, 0.022, 0.40, 16);
  const legPositions = [
    [ 0.20, 0.20,  0.20],
    [-0.20, 0.20,  0.20],
    [ 0.20, 0.20, -0.20],
    [-0.20, 0.20, -0.20],
  ];
  for (const [x, y, z] of legPositions) {
    const leg = new THREE.Mesh(legGeom, woodMat);
    leg.position.set(x, y, z);
    root.add(leg);
  }

  // Backrest — tall flat plate at the back of the seat.
  const backrestGeom = new THREE.BoxGeometry(0.45, 0.45, 0.025);
  const backrest = new THREE.Mesh(backrestGeom, woodMat);
  backrest.position.set(0, 0.65, -0.21);
  root.add(backrest);

  fitToUnitCube(THREE, root);
  return root;
}

function fitToUnitCube(THREE, root) {
  const box = new THREE.Box3().setFromObject(root);
  const size = new THREE.Vector3();
  const center = new THREE.Vector3();
  box.getSize(size);
  box.getCenter(center);
  const maxDim = Math.max(size.x, size.y, size.z) || 1;
  const scale = 0.95 / maxDim;
  root.scale.setScalar(scale);
  root.position.set(-center.x * scale, -center.y * scale, -center.z * scale);
}
```

Key idioms:
- Single shared material for visually-uniform objects.
- Radial-symmetric placement via explicit position list (4 corners) —
  cleaner than computing angles unless count is large.
- Backrest is a thin Z-axis box, not a tall vertical plate; orientation
  matters for rendering.
- `fitToUnitCube` with `0.95 / maxDim` is mandatory.

### Example 2 — Glass bottle (lathe profile, transmission glass)

Reference summary:
> A clear glass wine bottle with a bulbous body tapering to a long neck
> and a small lip at the top. Empty, transparent.

```javascript
export default function generate(THREE) {
  // Glass material — MeshPhysicalMaterial with transmission for
  // see-through behavior. metalness 0, low roughness.
  const glassMat = new THREE.MeshPhysicalMaterial({
    color: 0xddeedd,
    metalness: 0.0,
    roughness: 0.05,
    transmission: 0.95,
    ior: 1.5,
    transparent: true,
  });

  // Lathe profile — array of THREE.Vector2(radius, height) points
  // describing the silhouette from bottom to top. CRITICAL: must be
  // Vector2 instances, not [r, y] arrays — plain arrays produce NaN
  // vertices and an invisible mesh.
  const profile = [
    new THREE.Vector2(0.00, 0.00),  // closed bottom center
    new THREE.Vector2(0.18, 0.00),  // bottom edge
    new THREE.Vector2(0.18, 0.10),  // shoulder of body
    new THREE.Vector2(0.18, 0.45),  // body top (still wide)
    new THREE.Vector2(0.10, 0.55),  // body→neck transition
    new THREE.Vector2(0.05, 0.60),  // neck base
    new THREE.Vector2(0.05, 0.85),  // neck top
    new THREE.Vector2(0.06, 0.90),  // small lip flare
    new THREE.Vector2(0.00, 0.92),  // close top opening
  ];
  const bodyGeom = new THREE.LatheGeometry(profile, 32);
  const bottle = new THREE.Mesh(bodyGeom, glassMat);

  const root = new THREE.Group();
  root.add(bottle);

  fitToUnitCube(THREE, root);
  return root;
}

function fitToUnitCube(THREE, root) {
  const box = new THREE.Box3().setFromObject(root);
  const size = new THREE.Vector3();
  const center = new THREE.Vector3();
  box.getSize(size);
  box.getCenter(center);
  const maxDim = Math.max(size.x, size.y, size.z) || 1;
  const scale = 0.95 / maxDim;
  root.scale.setScalar(scale);
  root.position.set(-center.x * scale, -center.y * scale, -center.z * scale);
}
```

Key idioms:
- `new THREE.Vector2(r, y)` for every lathe profile point — NEVER
  `[r, y]` plain arrays.
- Profile starts at bottom (low y) and goes up; first/last points should
  have radius 0 if you want a closed shell.
- Glass uses `MeshPhysicalMaterial` with `transmission` + `ior` + `transparent`,
  not MeshStandardMaterial.
- Even single-part objects need a wrapping `Group` for `fitToUnitCube`.

### Example 3 — SUV (body, cabin, wheels, roof rack, spare tire)

Reference summary:
> A boxy mid-size SUV with a high roofline, chunky side steps, a roof rack,
> and a spare tire mounted on the rear door. Tan body paint, dark rubber
> wheels with chrome hub caps, and tinted glass windows.

```javascript
export default function generate(THREE) {
  const group = new THREE.Group();

  // --- dimension constants (all in local units before fitToUnitCube) ---
  const VW = 0.62, VL = 0.92;
  const wheelR = 0.042;
  const wheelBot = -0.23;
  const wheelCY = wheelBot + wheelR;
  const bodyBot = -0.17;
  const belt = 0.005;
  const roofBot = 0.16, roofTop = 0.185;
  const rackY = 0.20;
  const tireThick = wheelR * 0.35;
  const torusR = wheelR - tireThick;

  // --- materials: one per distinct surface class ---
  const bodyMat   = new THREE.MeshStandardMaterial({ color: 0xC8B896, roughness: 0.6, metalness: 0.1 });
  const blackMat  = new THREE.MeshStandardMaterial({ color: 0x222222, roughness: 0.7, metalness: 0.05 });
  const darkMat   = new THREE.MeshStandardMaterial({ color: 0x1A1A1A, roughness: 0.8, metalness: 0.05 });
  const chromeMat = new THREE.MeshStandardMaterial({ color: 0xC0C0C0, roughness: 0.1, metalness: 0.9 });
  const glassMat  = new THREE.MeshPhysicalMaterial({
    color: 0x8899AA, roughness: 0.1, metalness: 0.0,
    transmission: 0.5, transparent: true, opacity: 0.6,
  });
  const lensMat   = new THREE.MeshStandardMaterial({
    color: 0xFFFFDD, roughness: 0.3, metalness: 0.2,
    emissive: 0xFFFFDD, emissiveIntensity: 0.15,
  });
  const rackMat   = new THREE.MeshStandardMaterial({ color: 0x333333, roughness: 0.5, metalness: 0.3 });
  const tireMat   = new THREE.MeshStandardMaterial({ color: 0x1A1A1A, roughness: 0.9, metalness: 0.0 });
  const hubMat    = new THREE.MeshStandardMaterial({ color: 0x3A3A3A, roughness: 0.4, metalness: 0.6 });
  const tailMat   = new THREE.MeshStandardMaterial({
    color: 0xCC2222, roughness: 0.3, metalness: 0.1,
    emissive: 0xCC2222, emissiveIntensity: 0.1,
  });

  // Helper — avoids repeating new THREE.Mesh(BoxGeometry...) boilerplate.
  function addBox(w, h, d, mat, x, y, z) {
    const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mat);
    m.position.set(x, y, z);
    group.add(m);
    return m;
  }

  // Helper — thin structural tube between two Vector3 points.
  function addTube(p1, p2, r, mat) {
    const m = new THREE.Mesh(
      new THREE.TubeGeometry(new THREE.LineCurve3(p1, p2), 1, r, 6, false),
      mat
    );
    group.add(m);
  }

  // --- body ---
  const lbW = VW * 0.80, lbH = belt - bodyBot, lbL = VL * 0.88;
  addBox(lbW, lbH, lbL, bodyMat, 0, bodyBot + lbH / 2, 0);

  const cabW = VW * 0.74, cabH = roofBot - belt, cabL = VL * 0.58, cabZ = -VL * 0.08;
  addBox(cabW, cabH, cabL, bodyMat, 0, belt + cabH / 2, cabZ);

  const hoodL = VL * 0.20;
  addBox(lbW * 0.92, 0.012, hoodL, bodyMat, 0, belt, cabZ + cabL / 2 + hoodL / 2 + 0.005);

  addBox(VW * 0.70, roofTop - roofBot, cabL * 0.96, blackMat, 0, roofBot + (roofTop - roofBot) / 2, cabZ);

  // --- roof rack: outer frame + cross-bars + corner uprights via addTube ---
  const rkW = VW * 0.55, rkL = VL * 0.42, tubR = 0.004;
  const corners = [
    new THREE.Vector3(-rkW / 2, rackY, cabZ - rkL / 2),
    new THREE.Vector3( rkW / 2, rackY, cabZ - rkL / 2),
    new THREE.Vector3( rkW / 2, rackY, cabZ + rkL / 2),
    new THREE.Vector3(-rkW / 2, rackY, cabZ + rkL / 2),
  ];
  for (let i = 0; i < 4; i++) addTube(corners[i], corners[(i + 1) % 4], tubR, rackMat);
  for (let ci = 1; ci <= 3; ci++) {
    const cz = corners[0].z + (corners[3].z - corners[0].z) * (ci / 4);
    addTube(new THREE.Vector3(-rkW / 2, rackY, cz), new THREE.Vector3(rkW / 2, rackY, cz), tubR, rackMat);
  }
  for (const c of corners) addTube(c, new THREE.Vector3(c.x, roofTop + 0.002, c.z), tubR, rackMat);

  // --- glass ---
  const wsH = cabH * 0.78;
  addBox(VW * 0.64, wsH, 0.005, glassMat, 0, belt + cabH * 0.12 + wsH / 2, cabZ + cabL / 2 + 0.003);
  const rwH = wsH * 0.72;
  addBox(VW * 0.52, rwH, 0.005, glassMat, 0, belt + cabH * 0.16 + rwH / 2, cabZ - cabL / 2 - 0.003);

  // Side windows: two per side, iterated with ±1 pattern.
  const swH = cabH * 0.55, swY = belt + cabH * 0.22 + swH / 2;
  for (const side of [-1, 1]) {
    const sx = side * (cabW / 2 + 0.003);
    const swFrontL = cabL * 0.30, swRearL = cabL * 0.25, swBase = cabZ + cabL / 2 - cabL * 0.06;
    addBox(0.005, swH,          swFrontL, glassMat, sx, swY, swBase - swFrontL / 2);
    addBox(0.005, swH * 0.92,   swRearL,  glassMat, sx, swY, swBase - swFrontL - cabL * 0.05 - swRearL / 2);
  }

  // --- front grille + chrome slats ---
  const grW = VW * 0.34, grH = 0.055, grZ = lbL / 2 + 0.005, grY = bodyBot + lbH * 0.52;
  addBox(grW, grH, 0.012, darkMat, 0, grY, grZ);
  for (let si = 0; si < 5; si++) {
    addBox(grW * 0.84, grH * 0.08, 0.016, chromeMat, 0, grY - grH / 2 + grH * (si + 0.5) / 5, grZ + 0.004);
  }

  // --- headlights: CylinderGeometry rotated 90° to face forward ---
  const hlR = VW * 0.038;
  for (const hs of [-1, 1]) {
    const hx = hs * (grW / 2 + hlR + 0.018), hy = grY + 0.005;
    const rim = new THREE.Mesh(new THREE.CylinderGeometry(hlR + 0.005, hlR + 0.005, 0.008, 16), chromeMat);
    rim.rotation.x = Math.PI / 2;
    rim.position.set(hx, hy, grZ);
    group.add(rim);
    const lens = new THREE.Mesh(new THREE.CylinderGeometry(hlR, hlR, 0.012, 16), lensMat);
    lens.rotation.x = Math.PI / 2;
    lens.position.set(hx, hy, grZ + 0.002);
    group.add(lens);
  }

  // --- bumpers + fenders ---
  const bmpW = VW * 0.84, bmpH = 0.025, bmpD = 0.032;
  addBox(bmpW, bmpH, bmpD, blackMat, 0, bodyBot + bmpH / 2,  lbL / 2 + bmpD / 2);
  addBox(bmpW, bmpH, bmpD, blackMat, 0, bodyBot + bmpH / 2, -lbL / 2 - bmpD / 2);
  for (const fs of [-1, 1]) {
    addBox(0.012, lbH * 0.22, lbL * 0.9, blackMat, fs * (lbW / 2 + 0.005), bodyBot + lbH * 0.11, 0);
  }

  // --- wheels: TorusGeometry (tire) + CylinderGeometry (hub + cap),
  //     all rotated Math.PI/2 around Z so they face the X-axis. ---
  const wFZ =  VL * 0.30, wRZ = -VL * 0.30, wInX = lbW / 2;
  for (const [wx, wz] of [[-wInX, wFZ], [wInX, wFZ], [-wInX, wRZ], [wInX, wRZ]]) {
    const wy = wheelCY;
    const tire = new THREE.Mesh(new THREE.TorusGeometry(torusR, tireThick, 10, 24), tireMat);
    tire.rotation.z = Math.PI / 2;
    tire.position.set(wx, wy, wz);
    group.add(tire);

    const hub = new THREE.Mesh(new THREE.CylinderGeometry(wheelR * 0.42, wheelR * 0.42, 0.015, 12), hubMat);
    hub.rotation.z = Math.PI / 2;
    hub.position.set(wx, wy, wz);
    group.add(hub);

    const cap = new THREE.Mesh(new THREE.CylinderGeometry(wheelR * 0.12, wheelR * 0.12, 0.018, 8), chromeMat);
    cap.rotation.z = Math.PI / 2;
    cap.position.set(wx, wy, wz);
    group.add(cap);

    // Wheel arch
    const sideDir = wx > 0 ? 1 : -1;
    addBox(0.016, wheelR * 2.3, wheelR * 2.5, blackMat, wx + sideDir * 0.014, wy + wheelR * 0.35, wz);
  }

  // --- tail lights ---
  const tlW = 0.022, tlH = 0.032;
  for (const ts of [-1, 1]) {
    addBox(tlW, tlH, 0.008, tailMat, ts * (lbW / 2 - tlW * 0.6), bodyBot + lbH * 0.55, -lbL / 2 - 0.003);
  }

  // --- spare tire on rear door ---
  const spareThick = tireThick * 0.8;
  const spareTorusR = wheelR * 0.95 - spareThick;
  const spareZ = -lbL / 2 - bmpD - spareThick - 0.008;
  const spareY = bodyBot + lbH * 0.5;
  const spareTire = new THREE.Mesh(new THREE.TorusGeometry(spareTorusR, spareThick, 8, 20), tireMat);
  spareTire.rotation.x = Math.PI / 2;
  spareTire.position.set(0, spareY, spareZ);
  group.add(spareTire);
  const spareHub = new THREE.Mesh(new THREE.CylinderGeometry(wheelR * 0.35, wheelR * 0.35, 0.012, 10), hubMat);
  spareHub.rotation.x = Math.PI / 2;
  spareHub.position.set(0, spareY, spareZ);
  group.add(spareHub);

  // --- side steps + mirrors ---
  for (const ss of [-1, 1]) {
    addBox(0.028, 0.007, VL * 0.42, blackMat, ss * (lbW / 2 + 0.012), bodyBot + 0.008, 0);
    const mx = ss * (cabW / 2 + 0.018), mY = belt + cabH * 0.55, mZ = cabZ + cabL / 2 - cabL * 0.02;
    addBox(0.005, 0.018, 0.022, blackMat, mx, mY, mZ);
    addBox(0.003, 0.014, 0.018, glassMat, mx + ss * 0.003, mY, mZ);
  }

  fitToUnitCube(THREE, group);
  return group;
}

function fitToUnitCube(THREE, root) {
  const box = new THREE.Box3().setFromObject(root);
  const size = new THREE.Vector3();
  const center = new THREE.Vector3();
  box.getSize(size);
  box.getCenter(center);
  const maxDim = Math.max(size.x, size.y, size.z) || 1;
  const scale = 0.95 / maxDim;
  root.scale.setScalar(scale);
  root.position.set(-center.x * scale, -center.y * scale, -center.z * scale);
}
```

Key idioms:
- Multiple materials (one per surface class: body, glass, chrome, rubber, emissive
  tail/lens) — never one global material for a multi-surface object.
- `addBox` / `addTube` helpers eliminate repeated `new THREE.Mesh(...)` for
  symmetric parts — extract helpers whenever the same pattern appears 4+ times.
- Wheels: `TorusGeometry` (tire ring) + `CylinderGeometry` (hub disc),
  both with `rotation.z = Math.PI/2` so they face the X-axis, not Y-up.
- Roof rack rails and cross-bars use `TubeGeometry` with `LineCurve3` for
  thin structural lines — not BoxGeometry.
- Symmetric pairs (wheels, windows, fenders, mirrors, steps) use
  `for (const side of [-1, 1])` so count is explicit and easy to verify.
- Spare tyre on rear door faces forward → `rotation.x = Math.PI/2` (Y-axis
  wheel), unlike the road wheels which use `rotation.z = Math.PI/2`.
- `fitToUnitCube` is still mandatory even for large multi-part assemblies.

These three examples cover the most-failed patterns:
- Composing N-leg/N-spoke radial structures from a single geometry +
  position list.
- Lathe silhouettes with proper Vector2 control points.
- Multi-material, multi-part vehicles with helper functions, symmetric
  iteration, and correct wheel/tube geometry orientation.
- Picking the right material class for the surface type.
- Mandatory normalization at end.

When the reference image shows something else, follow the same composition
discipline: single shared materials when uniform, explicit position lists
for symmetric arrays, Vector2 for any 2D-points API, fitToUnitCube before
return.
"""
