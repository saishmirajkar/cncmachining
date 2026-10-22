import { GizmoHelper, GizmoViewport, OrbitControls } from "@react-three/drei";
import { Canvas, useThree } from "@react-three/fiber";
import { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import type { Mesh, Simulation } from "@/lib/types";
import { cn } from "@/lib/utils";

THREE.Object3D.DEFAULT_UP.set(0, 0, 1);

export interface Box3 {
  min: number[];
  max: number[];
  name?: string;
}

export interface WorkspaceProps {
  mesh?: Mesh | null;
  meshOffset?: [number, number, number];
  highlightFaces?: Set<number>;
  stock?: Box3 | null;
  keepOut?: Box3[];
  backplot?: Simulation["backplot"] | null;
  playIndex?: number | null; // number of segments drawn (null = all)
  toolDiameters?: Record<number, number>;
  heightmap?: Simulation["heightmap"] | null;
  showOrigin?: boolean;
  className?: string;
  layers?: Partial<Record<LayerKey, boolean>>;
}

type LayerKey = "part" | "stock" | "toolpath" | "rapids" | "heightmap" | "keepout";

const LAYER_LABELS: Record<LayerKey, string> = {
  part: "Part",
  stock: "Stock",
  toolpath: "Feed moves",
  rapids: "Rapids",
  heightmap: "Simulated stock",
  keepout: "Keep-out zones",
};

function useBounds(p: WorkspaceProps) {
  return useMemo(() => {
    const b = new THREE.Box3();
    const off = p.meshOffset ?? [0, 0, 0];
    if (p.mesh && p.mesh.positions.length) {
      const pos = p.mesh.positions;
      for (let i = 0; i < pos.length; i += 3) b.expandByPoint(new THREE.Vector3(pos[i] + off[0], pos[i + 1] + off[1], pos[i + 2] + off[2]));
    }
    if (p.mesh?.segments?.length) {
      const s = p.mesh.segments;
      for (let i = 0; i < s.length; i += 3) b.expandByPoint(new THREE.Vector3(s[i], s[i + 1], s[i + 2]));
    }
    if (p.stock) {
      b.expandByPoint(new THREE.Vector3(...(p.stock.min as [number, number, number])));
      b.expandByPoint(new THREE.Vector3(...(p.stock.max as [number, number, number])));
    }
    if (b.isEmpty()) b.set(new THREE.Vector3(-50, -50, -50), new THREE.Vector3(50, 50, 50));
    return b;
  }, [p.mesh, p.meshOffset, p.stock]);
}

function CameraFit({ bounds }: { bounds: THREE.Box3 }) {
  const { camera, controls } = useThree() as unknown as { camera: THREE.PerspectiveCamera; controls: { target: THREE.Vector3; update: () => void } | null };
  const fitted = useRef<string>("");
  useEffect(() => {
    const key = `${bounds.min.toArray()}${bounds.max.toArray()}`;
    if (fitted.current === key || !controls) return;
    fitted.current = key;
    const c = bounds.getCenter(new THREE.Vector3());
    const size = bounds.getSize(new THREE.Vector3()).length();
    camera.position.set(c.x + size * 0.75, c.y - size * 1.0, c.z + size * 0.8);
    camera.near = size / 1000;
    camera.far = size * 50;
    camera.updateProjectionMatrix();
    controls.target.copy(c);
    controls.update();
  }, [bounds, camera, controls]);
  return null;
}

function PartMesh({ mesh, offset, highlight, translucent }: { mesh: Mesh; offset: [number, number, number]; highlight?: Set<number>; translucent: boolean }) {
  const geom = useMemo(() => {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(mesh.positions, 3));
    g.setAttribute("normal", new THREE.Float32BufferAttribute(mesh.normals, 3));
    g.setIndex(mesh.indices);
    return g;
  }, [mesh]);
  const hiGeom = useMemo(() => {
    if (!highlight || highlight.size === 0) return null;
    const idx: number[] = [];
    for (const r of mesh.face_ranges) {
      if (!highlight.has(r.face)) continue;
      for (let t = r.start; t < r.start + r.count; t++) idx.push(mesh.indices[3 * t], mesh.indices[3 * t + 1], mesh.indices[3 * t + 2]);
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", geom.getAttribute("position"));
    g.setAttribute("normal", geom.getAttribute("normal"));
    g.setIndex(idx);
    return g;
  }, [highlight, mesh, geom]);
  const edges = useMemo(() => new THREE.EdgesGeometry(geom, 25), [geom]);
  return (
    <group position={offset}>
      <mesh geometry={geom}>
        <meshStandardMaterial color="#9aa7b8" metalness={0.35} roughness={0.55} transparent={translucent} opacity={translucent ? 0.55 : 1} side={THREE.DoubleSide} polygonOffset polygonOffsetFactor={1} />
      </mesh>
      <lineSegments geometry={edges}>
        <lineBasicMaterial color="#334155" transparent opacity={0.55} />
      </lineSegments>
      {hiGeom && (
        <mesh geometry={hiGeom}>
          <meshStandardMaterial color="#f59e0b" emissive="#b45309" emissiveIntensity={0.35} side={THREE.DoubleSide} polygonOffset polygonOffsetFactor={-1} />
        </mesh>
      )}
    </group>
  );
}

function Segments2D({ segments }: { segments: number[] }) {
  const geom = useMemo(() => {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(segments, 3));
    return g;
  }, [segments]);
  return (
    <lineSegments geometry={geom}>
      <lineBasicMaterial color="#38bdf8" />
    </lineSegments>
  );
}

function BoxOutline({ box, color, fill, opacity = 0.08 }: { box: Box3; color: string; fill?: boolean; opacity?: number }) {
  const size = box.max.map((v, i) => v - box.min[i]) as [number, number, number];
  const center = box.max.map((v, i) => (v + box.min[i]) / 2) as [number, number, number];
  const geom = useMemo(() => new THREE.BoxGeometry(...size), [size[0], size[1], size[2]]); // eslint-disable-line react-hooks/exhaustive-deps
  const edges = useMemo(() => new THREE.EdgesGeometry(geom), [geom]);
  return (
    <group position={center}>
      {fill && (
        <mesh geometry={geom}>
          <meshBasicMaterial color={color} transparent opacity={opacity} depthWrite={false} />
        </mesh>
      )}
      <lineSegments geometry={edges}>
        <lineBasicMaterial color={color} />
      </lineSegments>
    </group>
  );
}

/** Screen-sized text label drawn to a canvas texture (no DOM overlay, no font download). */
function Label({ text, position, color = "#ffffff", anchor = "center" }: { text: string; position: [number, number, number]; color?: string; anchor?: "center" | "left" }) {
  const { texture, aspect } = useMemo(() => {
    const scale = 4;
    const c = document.createElement("canvas");
    const ctx = c.getContext("2d")!;
    const font = `600 ${11 * scale}px "JetBrains Mono", ui-monospace, monospace`;
    ctx.font = font;
    const pad = 4 * scale;
    c.width = Math.ceil(ctx.measureText(text).width + pad * 2);
    c.height = 18 * scale;
    ctx.font = font;
    ctx.fillStyle = "rgba(0,0,0,0.6)";
    ctx.beginPath();
    ctx.roundRect(0, 0, c.width, c.height, 3 * scale);
    ctx.fill();
    ctx.fillStyle = color;
    ctx.textBaseline = "middle";
    ctx.fillText(text, pad, c.height / 2 + scale);
    const t = new THREE.CanvasTexture(c);
    t.colorSpace = THREE.SRGBColorSpace;
    return { texture: t, aspect: c.width / c.height };
  }, [text, color]);
  useEffect(() => () => texture.dispose(), [texture]);
  const h = 0.035; // fraction of the viewport height (sizeAttenuation off)
  return (
    <sprite position={position} scale={[h * aspect, h, 1]} center={anchor === "left" ? new THREE.Vector2(-0.1, 0.5) : new THREE.Vector2(0.5, 0.5)} renderOrder={10}>
      <spriteMaterial map={texture} sizeAttenuation={false} depthTest={false} transparent />
    </sprite>
  );
}

function OriginTriad({ size }: { size: number }) {
  const axes: [string, [number, number, number], string][] = [
    ["X", [size, 0, 0], "#ef4444"],
    ["Y", [0, size, 0], "#22c55e"],
    ["Z", [0, 0, size], "#3b82f6"],
  ];
  return (
    <group>
      {axes.map(([name, end, color]) => {
        const g = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(0, 0, 0), new THREE.Vector3(...end)]);
        return (
          <group key={name}>
            <lineSegments geometry={g}>
              <lineBasicMaterial color={color} linewidth={2} />
            </lineSegments>
            <Label text={name} position={end} color={color} />
          </group>
        );
      })}
      <Label text="WCS 0,0,0" position={[0, 0, 0]} anchor="left" />
    </group>
  );
}

function Backplot({ bp, playIndex, showRapids, showFeeds }: { bp: NonNullable<Simulation["backplot"]>; playIndex: number | null; showRapids: boolean; showFeeds: boolean }) {
  const { feedGeom, rapidGeom, feedMap, rapidMap } = useMemo(() => {
    const f: number[] = [];
    const r: number[] = [];
    const fc: number[] = [];
    const fm: number[] = [];
    const rm: number[] = [];
    const palette = ["#38bdf8", "#a78bfa", "#34d399", "#f472b6", "#facc15", "#60a5fa", "#fb7185"];
    for (let i = 0; i < bp.kinds.length; i++) {
      const p = bp.positions.slice(6 * i, 6 * i + 6);
      if (bp.kinds[i] === 1) {
        f.push(...p);
        const c = new THREE.Color(palette[(bp.ops[i] - 1 + palette.length) % palette.length]);
        fc.push(c.r, c.g, c.b, c.r, c.g, c.b);
        fm.push(i);
      } else {
        r.push(...p);
        rm.push(i);
      }
    }
    const fg = new THREE.BufferGeometry();
    fg.setAttribute("position", new THREE.Float32BufferAttribute(f, 3));
    fg.setAttribute("color", new THREE.Float32BufferAttribute(fc, 3));
    const rg = new THREE.BufferGeometry();
    rg.setAttribute("position", new THREE.Float32BufferAttribute(r, 3));
    const lr = new THREE.LineSegments(rg);
    lr.computeLineDistances();
    return { feedGeom: fg, rapidGeom: lr.geometry, feedMap: fm, rapidMap: rm };
  }, [bp]);
  // draw range by playback index (segments are in program order)
  const n = playIndex ?? bp.kinds.length;
  const fCount = upperBound(feedMap, n);
  const rCount = upperBound(rapidMap, n);
  feedGeom.setDrawRange(0, fCount * 2);
  rapidGeom.setDrawRange(0, rCount * 2);
  return (
    <group>
      {showFeeds && (
        <lineSegments geometry={feedGeom}>
          <lineBasicMaterial vertexColors />
        </lineSegments>
      )}
      {showRapids && (
        <lineSegments geometry={rapidGeom}>
          <lineDashedMaterial color="#f97316" dashSize={2} gapSize={1.5} transparent opacity={0.85} />
        </lineSegments>
      )}
    </group>
  );
}

function upperBound(arr: number[], n: number) {
  let lo = 0;
  let hi = arr.length;
  while (lo < hi) {
    const m = (lo + hi) >> 1;
    if (arr[m] < n) lo = m + 1;
    else hi = m;
  }
  return lo;
}

function ToolMarker({ bp, playIndex, diameters }: { bp: NonNullable<Simulation["backplot"]>; playIndex: number; diameters: Record<number, number> }) {
  const i = Math.min(Math.max(playIndex - 1, 0), bp.kinds.length - 1);
  const p = bp.positions.slice(6 * i + 3, 6 * i + 6);
  const d = diameters[bp.tools[i]] ?? 6;
  return (
    <group position={[p[0], p[1], p[2]]}>
      <mesh position={[0, 0, 15]} rotation={[Math.PI / 2, 0, 0]}>
        <cylinderGeometry args={[d / 2, d / 2, 30, 32]} />
        <meshStandardMaterial color="#e2e8f0" metalness={0.8} roughness={0.25} transparent opacity={0.85} />
      </mesh>
    </group>
  );
}

function HeightMap({ hm }: { hm: NonNullable<Simulation["heightmap"]> }) {
  const geom = useMemo(() => {
    const { nx, ny, x0, y0, dx, z } = hm;
    const pos = new Float32Array(nx * ny * 3);
    const col = new Float32Array(nx * ny * 3);
    let zmin = Infinity;
    let zmax = -Infinity;
    for (const v of z) if (v !== null) {
      zmin = Math.min(zmin, v);
      zmax = Math.max(zmax, v);
    }
    const lo = new THREE.Color("#1e3a5f");
    const hi = new THREE.Color("#cbd5e1");
    for (let j = 0; j < ny; j++)
      for (let i = 0; i < nx; i++) {
        const k = j * nx + i;
        const v = z[k] ?? hm.stock_bottom;
        pos.set([x0 + i * dx, y0 + j * dx, v], 3 * k);
        const t = zmax > zmin ? (v - zmin) / (zmax - zmin) : 1;
        const c = lo.clone().lerp(hi, t);
        col.set([c.r, c.g, c.b], 3 * k);
      }
    const idx: number[] = [];
    for (let j = 0; j < ny - 1; j++)
      for (let i = 0; i < nx - 1; i++) {
        const a = j * nx + i;
        if (z[a] === null || z[a + 1] === null || z[a + nx] === null || z[a + nx + 1] === null) continue;
        idx.push(a, a + 1, a + nx + 1, a, a + nx + 1, a + nx);
      }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    g.setAttribute("color", new THREE.BufferAttribute(col, 3));
    g.setIndex(idx);
    g.computeVertexNormals();
    return g;
  }, [hm]);
  return (
    <mesh geometry={geom}>
      <meshStandardMaterial vertexColors roughness={0.7} metalness={0.1} side={THREE.DoubleSide} />
    </mesh>
  );
}

export function Workspace3D(p: WorkspaceProps) {
  const bounds = useBounds(p);
  const size = bounds.getSize(new THREE.Vector3()).length();
  const [layers, setLayers] = useState<Record<LayerKey, boolean>>({
    part: true,
    stock: true,
    toolpath: true,
    rapids: true,
    heightmap: false,
    keepout: true,
    ...p.layers,
  });
  const available: LayerKey[] = [
    ...(p.mesh ? (["part"] as const) : []),
    ...(p.stock ? (["stock"] as const) : []),
    ...(p.backplot ? (["toolpath", "rapids"] as const) : []),
    ...(p.heightmap ? (["heightmap"] as const) : []),
    ...(p.keepOut?.length ? (["keepout"] as const) : []),
  ];
  const off = p.meshOffset ?? [0, 0, 0];
  return (
    <div className={cn("relative overflow-hidden rounded-lg border border-border bg-viewport", p.className)}>
      <Canvas camera={{ fov: 35, up: [0, 0, 1] }} dpr={[1, 2]} gl={{ antialias: true, preserveDrawingBuffer: true }}>
        <color attach="background" args={["#141b26"]} />
        <hemisphereLight args={["#e2e8f0", "#1e293b", 0.9]} />
        <directionalLight position={[size, -size, size * 1.5]} intensity={1.6} />
        <directionalLight position={[-size, size, size * 0.5]} intensity={0.4} />
        <OrbitControls makeDefault enableDamping dampingFactor={0.15} />
        <CameraFit bounds={bounds} />
        <gridHelper args={[Math.ceil(size / 10) * 20, 20, "#334155", "#1f2937"]} rotation={[Math.PI / 2, 0, 0]} position={[bounds.getCenter(new THREE.Vector3()).x, bounds.getCenter(new THREE.Vector3()).y, bounds.min.z - 0.01]} />
        {p.mesh && layers.part && p.mesh.positions.length > 0 && (
          <PartMesh mesh={p.mesh} offset={off} highlight={p.highlightFaces} translucent={Boolean(p.backplot && layers.toolpath)} />
        )}
        {p.mesh?.segments && <Segments2D segments={p.mesh.segments} />}
        {p.stock && layers.stock && <BoxOutline box={p.stock} color="#94a3b8" fill opacity={0.05} />}
        {p.keepOut && layers.keepout && p.keepOut.map((b, i) => <BoxOutline key={i} box={b} color="#ef4444" fill opacity={0.18} />)}
        {p.heightmap && layers.heightmap && <HeightMap hm={p.heightmap} />}
        {p.backplot && (
          <Backplot bp={p.backplot} playIndex={p.playIndex ?? null} showFeeds={layers.toolpath} showRapids={layers.rapids} />
        )}
        {p.backplot && p.playIndex != null && p.playIndex > 0 && (
          <ToolMarker bp={p.backplot} playIndex={p.playIndex} diameters={p.toolDiameters ?? {}} />
        )}
        {(p.showOrigin ?? true) && <OriginTriad size={Math.max(5, size * 0.08)} />}
        <GizmoHelper alignment="bottom-right" margin={[64, 64]}>
          <GizmoViewport axisColors={["#ef4444", "#22c55e", "#3b82f6"]} labelColor="white" />
        </GizmoHelper>
      </Canvas>
      {available.length > 1 && (
        <div className="absolute top-2 left-2 flex flex-wrap gap-1">
          {available.map((k) => (
            <button
              key={k}
              onClick={() => setLayers((l) => ({ ...l, [k]: !l[k] }))}
              className={cn(
                "rounded border px-2 py-0.5 text-[11px] font-medium backdrop-blur",
                layers[k] ? "border-sky-400/40 bg-sky-400/15 text-sky-100" : "border-white/10 bg-black/30 text-slate-400",
              )}
            >
              {LAYER_LABELS[k]}
            </button>
          ))}
        </div>
      )}
      <div className="pointer-events-none absolute bottom-2 left-2 rounded bg-black/40 px-2 py-1 font-mono text-[10px] text-slate-300">
        Drag: rotate · Right-drag: pan · Wheel: zoom · units mm
      </div>
    </div>
  );
}
