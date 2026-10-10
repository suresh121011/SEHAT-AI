import assert from "node:assert/strict";
import { test } from "node:test";

import { REGIONS, labelOf, sanitize, toggle } from "./bodyMap.ts";
import { activateCase, clearAllLocalNotes, clearNotes, readNotes, writeNotes } from "./intakeStore.ts";
import { availability, handoffDue, hrefFor, nextStep } from "./steps.ts";

class MemoryStorage {
  private m = new Map<string, string>();
  get length() { return this.m.size; }
  key(i: number) { return [...this.m.keys()][i] ?? null; }
  getItem(k: string) { return this.m.get(k) ?? null; }
  setItem(k: string, v: string) { this.m.set(k, v); }
  removeItem(k: string) { this.m.delete(k); }
}

test("body map: toggle twice is a no-op, unknown regions are ignored", () => {
  assert.deepEqual(toggle(toggle([], "chest_left"), "chest_left"), []);
  assert.deepEqual(toggle(["chest_left"], "not_a_region"), ["chest_left"]);
  assert.deepEqual(sanitize(["chest_left", "chest_left", 7, "nope"]), ["chest_left"]);
});

test("body map: every region has a unique id and a patient-side label", () => {
  const ids = REGIONS.map((r) => r.id);
  assert.equal(new Set(ids).size, ids.length);
  assert.match(labelOf("chest_left"), /patient's left/);
  // front view: the patient's right side is drawn on the viewer's left
  const right = REGIONS.find((r) => r.id === "arm_right_front")!.shape;
  const left = REGIONS.find((r) => r.id === "arm_left_front")!.shape;
  assert.ok(right.kind === "rect" && left.kind === "rect" && right.x < left.x);
});

test("local notes are per case and cleared when another case is opened", () => {
  const s = new MemoryStorage();
  activateCase("A", s);
  writeNotes("A", { bodyMap: ["chest_left"], followUps: {} }, s);
  assert.deepEqual(readNotes("A", s).bodyMap, ["chest_left"]);
  activateCase("B", s); // device handed to the next patient
  assert.deepEqual(readNotes("A", s).bodyMap, []);
  writeNotes("B", { bodyMap: ["head_front"], followUps: {} }, s);
  clearNotes("B", s);
  assert.deepEqual(readNotes("B", s), { bodyMap: [], followUps: {} });
  writeNotes("B", { bodyMap: ["head_front"], followUps: {} }, s);
  clearAllLocalNotes(s);
  assert.equal(s.length, 0);
});

test("corrupt stored notes are ignored, not trusted", () => {
  const s = new MemoryStorage();
  s.setItem("sehat:notes:A", "{not json");
  assert.deepEqual(readNotes("A", s), { bodyMap: [], followUps: {} });
});

test("steps: patients never unlock health-worker steps; nothing past consent without triage consent", () => {
  assert.equal(availability("review", "patient", "granted"), "health_worker_only");
  assert.equal(availability("followup", "patient", "granted"), "health_worker_only");
  assert.equal(availability("documents", "patient", "granted"), "open"); // patients upload their own reports
  assert.equal(availability("documents", "patient", "not_provided"), "needs_consent");
  assert.equal(availability("voice", "anm", "not_provided"), "needs_consent");
  assert.equal(availability("voice", "anm", "withdrawn"), "needs_consent");
  assert.equal(availability("review", "anm", "granted"), "open");
  assert.equal(nextStep("body", "patient", "granted"), "documents");
  assert.equal(nextStep("documents", "patient", "granted"), null);
  assert.equal(nextStep("body", "anm", "granted"), "documents");
  assert.equal(hrefFor("voice", "abc"), "/intake/voice?case=abc");
  assert.equal(hrefFor("case", "abc"), "/intake");
});

test("steps: a patient's last usable step shows the handoff; health workers and earlier steps never do", () => {
  assert.equal(handoffDue("documents", "patient", "granted"), true);
  assert.equal(handoffDue("body", "patient", "granted"), false); // reports still ahead
  assert.equal(handoffDue("body", "patient", "withdrawn"), false); // consent screen handles this
  assert.equal(handoffDue("consent", "patient", "granted"), false);
  assert.equal(handoffDue("body", "anm", "granted"), false);
  assert.equal(handoffDue("review", "anm", "granted"), false);
});

test("route guard: a patient may open the Reports page; reviewer dashboards stay staff-only", async () => {
  const { allowedRoles } = await import("./auth.ts");
  assert.ok(allowedRoles("/intake/documents")?.includes("patient"));
  assert.ok(allowedRoles("/intake/documents")?.includes("anm"));
  assert.ok(!allowedRoles("/dashboard")?.includes("patient"));
});
