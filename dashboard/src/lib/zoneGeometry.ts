// Zone outlines are stored in camera pixels (e.g. 2688 x 1520). The editor shows the picture smaller, so every
// click is scaled from the displayed size to camera pixels. The checks mirror app/routes/zones.py, so a bad
// outline is explained before it is sent.
import type { Point, ZoneType } from "./types";

export interface Box {
  left: number;
  top: number;
  width: number;
  height: number;
}

/** A click at (clientX, clientY) on a picture shown in `box` -> whole camera pixels, kept inside the frame. */
export function toCameraPixels(clientX: number, clientY: number, box: Box, frameW: number, frameH: number): Point {
  const x = ((clientX - box.left) / box.width) * frameW;
  const y = ((clientY - box.top) / box.height) * frameH;
  return [Math.round(Math.min(Math.max(x, 0), frameW)), Math.round(Math.min(Math.max(y, 0), frameH))];
}

export function area(points: Point[]): number {
  let a = 0;
  for (let i = 0; i < points.length; i++) {
    const [x1, y1] = points[i === 0 ? points.length - 1 : i - 1];
    const [x2, y2] = points[i];
    a += x2 * y1 - x1 * y2;
  }
  return Math.abs(a) / 2;
}

/** Middle of the outline (average of its points), for the label. */
export function centre(points: Point[]): Point {
  const n = points.length || 1;
  return [points.reduce((s, p) => s + p[0], 0) / n, points.reduce((s, p) => s + p[1], 0) / n];
}

function orient(a: Point, b: Point, c: Point): number {
  return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);
}

function onSegment(a: Point, b: Point, c: Point): boolean {
  return Math.min(a[0], b[0]) <= c[0] && c[0] <= Math.max(a[0], b[0])
    && Math.min(a[1], b[1]) <= c[1] && c[1] <= Math.max(a[1], b[1]);
}

function segmentsCross(p1: Point, p2: Point, q1: Point, q2: Point): boolean {
  const d1 = orient(q1, q2, p1), d2 = orient(q1, q2, p2), d3 = orient(p1, p2, q1), d4 = orient(p1, p2, q2);
  if (((d1 > 0 && d2 < 0) || (d1 < 0 && d2 > 0)) && ((d3 > 0 && d4 < 0) || (d3 < 0 && d4 > 0))) return true;
  return (d1 === 0 && onSegment(q1, q2, p1)) || (d2 === 0 && onSegment(q1, q2, p2))
    || (d3 === 0 && onSegment(p1, p2, q1)) || (d4 === 0 && onSegment(p1, p2, q2));
}

export const MAX_POINTS = 100;

/** Plain-language problem with the outline, or null if the server will accept it. */
export function outlineProblem(points: Point[], frameW: number, frameH: number): string | null {
  if (points.length < 3) return "An area needs at least 3 points.";
  if (points.length > MAX_POINTS) return `An area can have at most ${MAX_POINTS} points.`;
  if (points.some(([x, y]) => x < 0 || y < 0 || x > frameW || y > frameH)) {
    return "A point is outside the camera picture.";
  }
  if (area(points) < 1) return "The outline has no area (the points are on one line).";
  const n = points.length;
  for (let i = 0; i < n; i++) {
    for (let j = i + 1; j < n; j++) {
      if (j === i + 1 || (i === 0 && j === n - 1)) continue; // neighbouring edges share a corner
      if (segmentsCross(points[i], points[(i + 1) % n], points[j], points[(j + 1) % n])) {
        return "The outline crosses itself. Place the points in order around the area.";
      }
    }
  }
  return null;
}

export const ZONE_TYPE_LABEL: Record<ZoneType, string> = {
  prep: "Food preparation area",
  waste: "Waste bin area",
  ignore: "Ignored area (customers / outside)",
};

export const ZONE_TYPE_HINT: Record<ZoneType, string> = {
  prep: "Hair cover, mask, apron and phone rules are checked here.",
  waste: "The open-bin rule is checked here.",
  ignore: "Nothing is detected here, e.g. customers on the other side of the counter.",
};
