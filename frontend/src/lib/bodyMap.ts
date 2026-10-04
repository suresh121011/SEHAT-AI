// Body-map regions (patient-indicated location of a symptom). Pure data + selection helpers; no diagnosis, no SNOMED
// mapping (the architecture mentions SNOMED body sites, but no backend schema exists to receive them).
// Sides are the PATIENT'S left/right: on a front view the patient's left is on the viewer's right.

export type View = "front" | "back";
export type Region = { id: string; label: string; view: View; shape: { kind: "ellipse"; cx: number; cy: number; rx: number; ry: number } | { kind: "rect"; x: number; y: number; w: number; h: number; r: number } };

// viewBox 0 0 200 420. Front: patient's right arm is on the viewer's left (x < 100).
const R = (id: string, label: string, view: View, x: number, y: number, w: number, h: number): Region => ({ id, label, view, shape: { kind: "rect", x, y, w, h, r: 10 } });

export const REGIONS: Region[] = [
  { id: "head_front", label: "Head and face", view: "front", shape: { kind: "ellipse", cx: 100, cy: 34, rx: 24, ry: 28 } },
  R("neck_front", "Neck (front)", "front", 86, 62, 28, 16),
  R("chest_right", "Chest, patient's right side", "front", 62, 80, 37, 52),
  R("chest_left", "Chest, patient's left side", "front", 101, 80, 37, 52),
  R("abdomen_upper", "Upper abdomen (stomach area)", "front", 64, 134, 72, 36),
  R("abdomen_lower", "Lower abdomen", "front", 64, 172, 72, 34),
  R("pelvis_front", "Pelvis and groin", "front", 66, 208, 68, 26),
  R("arm_right_front", "Right arm", "front", 30, 84, 28, 110),
  R("arm_left_front", "Left arm", "front", 142, 84, 28, 110),
  R("hand_right_front", "Right hand", "front", 24, 198, 30, 30),
  R("hand_left_front", "Left hand", "front", 146, 198, 30, 30),
  R("leg_right_front", "Right leg", "front", 66, 238, 32, 140),
  R("leg_left_front", "Left leg", "front", 102, 238, 32, 140),
  R("foot_right_front", "Right foot", "front", 60, 382, 36, 26),
  R("foot_left_front", "Left foot", "front", 104, 382, 36, 26),
  // Back view: the patient's left is on the viewer's left.
  { id: "head_back", label: "Back of head", view: "back", shape: { kind: "ellipse", cx: 100, cy: 34, rx: 24, ry: 28 } },
  R("neck_back", "Back of neck", "back", 86, 62, 28, 16),
  R("back_upper", "Upper back", "back", 62, 80, 76, 56),
  R("back_lower", "Lower back", "back", 64, 138, 72, 66),
  R("buttocks", "Buttocks", "back", 66, 206, 68, 30),
  R("arm_left_back", "Left arm (back)", "back", 30, 84, 28, 110),
  R("arm_right_back", "Right arm (back)", "back", 142, 84, 28, 110),
  R("leg_left_back", "Left leg (back)", "back", 66, 238, 32, 140),
  R("leg_right_back", "Right leg (back)", "back", 102, 238, 32, 140),
];

const IDS = new Set(REGIONS.map((r) => r.id));

export function labelOf(id: string): string {
  return REGIONS.find((r) => r.id === id)?.label ?? id;
}

export function toggle(selected: string[], id: string): string[] {
  if (!IDS.has(id)) return selected; // unknown ids are ignored
  return selected.includes(id) ? selected.filter((x) => x !== id) : [...selected, id];
}

export function sanitize(selected: unknown): string[] {
  return Array.isArray(selected) ? [...new Set(selected.filter((x): x is string => typeof x === "string" && IDS.has(x)))] : [];
}
