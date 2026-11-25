// The area editor's picture: the camera snapshot (or a grid if there is none) with the areas drawn on top.
// The SVG's own coordinates are camera pixels, so outlines line up whatever size the picture is shown at;
// clicks and drags are converted from the displayed size with toCameraPixels().
import { useEffect, useRef, useState, type PointerEvent } from "react";
import { ZONE_TYPE_LABEL, toCameraPixels } from "../lib/zoneGeometry";
import type { Point, Zone, ZoneType } from "../lib/types";

export type Picture =
  | { status: "loading" }
  | { status: "ready"; url: string }
  | { status: "offline" | "off" | "error"; message: string };

interface Props {
  frameW: number;
  frameH: number;
  picture: Picture;
  zones: Zone[];
  selectedId: number | null;
  /** "draw": clicks add corners; "shape": corners of `draft` can be dragged; "view": click an area to select it. */
  mode: "view" | "draw" | "shape";
  draft: Point[];
  draftType: ZoneType;
  onSelect: (zoneId: number | null) => void;
  onAddPoint: (p: Point) => void;
  onMovePoint: (index: number, p: Point) => void;
  onMoveEnd: () => void;
  onCloseOutline: () => void;
}

export const ZONE_STYLE: Record<ZoneType, { stroke: string; fill: string; swatch: string }> = {
  prep: { stroke: "#16a34a", fill: "rgba(22, 163, 74, 0.16)", swatch: "bg-green-600" },
  waste: { stroke: "#ea580c", fill: "rgba(234, 88, 12, 0.22)", swatch: "bg-orange-600" },
  ignore: { stroke: "#64748b", fill: "url(#rads-hatch)", swatch: "bg-slate-500" },
};

const ORDER: Record<ZoneType, number> = { prep: 0, waste: 1, ignore: 2 };

function pointsAttr(points: Point[]): string {
  return points.map(([x, y]) => `${x},${y}`).join(" ");
}

function topLeft(points: Point[]): Point {
  return [Math.min(...points.map((p) => p[0])), Math.min(...points.map((p) => p[1]))];
}

export function ZoneCanvas(props: Props) {
  const { frameW: W, frameH: H, picture, zones, selectedId, mode, draft, draftType } = props;
  const svgRef = useRef<SVGSVGElement>(null);
  const [shownWidth, setShownWidth] = useState(800);
  const drag = useRef<{ index: number; moved: boolean } | null>(null);

  // Handles and labels keep the same size on screen whatever size the picture is shown at.
  useEffect(() => {
    const el = svgRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setShownWidth(el.getBoundingClientRect().width || 800));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const px = W / shownWidth; // camera pixels per screen pixel

  const at = (e: { clientX: number; clientY: number }): Point =>
    toCameraPixels(e.clientX, e.clientY, svgRef.current!.getBoundingClientRect(), W, H);

  const startDrag = (index: number) => (e: PointerEvent<SVGCircleElement>) => {
    e.stopPropagation();
    e.currentTarget.setPointerCapture(e.pointerId);
    drag.current = { index, moved: false };
  };
  const moveDrag = (e: PointerEvent<SVGCircleElement>) => {
    if (!drag.current) return;
    drag.current.moved = true;
    props.onMovePoint(drag.current.index, at(e));
  };
  const endDrag = (e: PointerEvent<SVGCircleElement>) => {
    const d = drag.current;
    drag.current = null;
    e.currentTarget.releasePointerCapture(e.pointerId);
    if (!d) return;
    if (d.moved) props.onMoveEnd();
    else if (mode === "draw" && d.index === 0 && draft.length >= 3) props.onCloseOutline(); // click on first corner
  };

  const editing = mode !== "view";
  const visible = [...zones]
    .filter((z) => !(mode === "shape" && z.zone_id === selectedId)) // its outline is the draft while reshaping
    .sort((a, b) => (a.zone_id === selectedId ? 1 : 0) - (b.zone_id === selectedId ? 1 : 0)
      || ORDER[a.type] - ORDER[b.type]);
  const font = 13 * px;
  const handleR = 7 * px;

  return (
    <div className="relative w-full overflow-hidden rounded-lg bg-slate-900 select-none"
      style={{ aspectRatio: `${W} / ${H}` }}>
      <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img"
        aria-label="Camera picture with the areas drawn on it"
        className={`absolute inset-0 h-full w-full touch-none ${mode === "draw" ? "cursor-crosshair" : ""}`}
        onClick={(e) => {
          if (mode === "draw") props.onAddPoint(at(e));
          else if (mode === "view") props.onSelect(null);
        }}>
        <defs>
          <pattern id="rads-hatch" width={18 * px} height={18 * px} patternUnits="userSpaceOnUse"
            patternTransform="rotate(45)">
            <rect width={18 * px} height={18 * px} fill="rgba(71, 85, 105, 0.35)" />
            <line x1="0" y1="0" x2="0" y2={18 * px} stroke="rgba(226, 232, 240, 0.75)" strokeWidth={6 * px} />
          </pattern>
          <pattern id="rads-grid" width={W / 16} height={W / 16} patternUnits="userSpaceOnUse">
            <path d={`M ${W / 16} 0 L 0 0 0 ${W / 16}`} fill="none" stroke="rgba(148, 163, 184, 0.25)"
              strokeWidth={1.5 * px} />
          </pattern>
        </defs>

        {picture.status === "ready" ? (
          <image href={picture.url} x="0" y="0" width={W} height={H} preserveAspectRatio="none" />
        ) : (
          <>
            <rect width={W} height={H} fill="#0f172a" />
            <rect width={W} height={H} fill="url(#rads-grid)" />
            <text x={W / 2} y={H / 2 - font} textAnchor="middle" fontSize={font * 1.25} fill="#e2e8f0"
              fontWeight="600">
              {picture.status === "loading" ? "Loading the camera picture…" : "No camera picture"}
            </text>
            {picture.status !== "loading" && (
              <text x={W / 2} y={H / 2 + font * 0.8} textAnchor="middle" fontSize={font} fill="#94a3b8">
                {picture.message} You can still draw on the grid ({W} × {H} camera pixels).
              </text>
            )}
          </>
        )}

        {visible.map((z) => {
          const style = ZONE_STYLE[z.type];
          const selected = z.zone_id === selectedId;
          const [lx, ly] = topLeft(z.polygon);
          const lines = [z.name, z.type === "ignore" ? ZONE_TYPE_LABEL.ignore : null].filter(Boolean) as string[];
          const boxW = Math.max(...lines.map((l) => l.length)) * font * 0.56 + font;
          return (
            <g key={z.zone_id} opacity={z.active ? 1 : 0.55}
              className={mode === "view" ? "cursor-pointer" : "pointer-events-none"}
              onClick={(e) => {
                if (mode !== "view") return;
                e.stopPropagation();
                props.onSelect(z.zone_id);
              }}>
              <polygon points={pointsAttr(z.polygon)} fill={style.fill} stroke={style.stroke}
                strokeWidth={selected ? 3.5 : 2} vectorEffect="non-scaling-stroke"
                strokeDasharray={z.active ? undefined : "6 5"} strokeLinejoin="round" />
              <rect x={lx + 6 * px} y={ly + 6 * px} width={boxW} height={lines.length * font * 1.3 + font * 0.5}
                rx={4 * px} fill="rgba(15, 23, 42, 0.78)" />
              {lines.map((l, i) => (
                <text key={l} x={lx + 6 * px + font * 0.5} y={ly + 6 * px + font * (1.2 + i * 1.3)} fontSize={font}
                  fill={i === 0 ? "#fff" : "#cbd5e1"} fontWeight={i === 0 ? 600 : 400}>
                  {i === 0 && !z.active ? `${l} (inactive)` : l}
                </text>
              ))}
            </g>
          );
        })}

        {editing && draft.length > 0 && (
          <g>
            {draft.length >= 3 || mode === "shape" ? (
              <polygon points={pointsAttr(draft)} fill={ZONE_STYLE[draftType].fill} stroke={ZONE_STYLE[draftType].stroke}
                strokeWidth={3} vectorEffect="non-scaling-stroke" strokeDasharray="8 6" className="pointer-events-none" />
            ) : (
              <polyline points={pointsAttr(draft)} fill="none" stroke={ZONE_STYLE[draftType].stroke} strokeWidth={3}
                vectorEffect="non-scaling-stroke" strokeDasharray="8 6" className="pointer-events-none" />
            )}
            {draft.map(([x, y], i) => (
              <circle key={i} cx={x} cy={y} r={i === 0 && mode === "draw" && draft.length >= 3 ? handleR * 1.4 : handleR}
                fill={i === 0 && mode === "draw" ? ZONE_STYLE[draftType].stroke : "#fff"}
                stroke={ZONE_STYLE[draftType].stroke} strokeWidth={2} vectorEffect="non-scaling-stroke"
                className="cursor-grab active:cursor-grabbing"
                onPointerDown={startDrag(i)} onPointerMove={moveDrag} onPointerUp={endDrag}
                onClick={(e) => e.stopPropagation()}>
                <title>{i === 0 && mode === "draw" ? "First corner: click it to close the outline" : `Corner ${i + 1}: drag to move`}</title>
              </circle>
            ))}
          </g>
        )}
      </svg>
    </div>
  );
}
