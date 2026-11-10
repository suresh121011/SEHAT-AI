// Run: node --test src/lib/deviceVoice.test.ts
import { test } from "node:test";
import assert from "node:assert/strict";

import { pickDeviceVoice } from "./deviceVoice.ts";

const v = (name: string, lang: string, localService: boolean) => ({ name, lang, localService });

test("a cloud browser voice is never chosen, even when it is the only match (V1)", () => {
  // Chrome's "Google हिन्दी" and Edge's "Online (Natural)" voices send the text to Google / Microsoft.
  assert.equal(pickDeviceVoice([v("Google हिन्दी", "hi-IN", false)], "hi"), null);
  assert.equal(pickDeviceVoice([v("Microsoft Swara Online (Natural)", "hi-IN", false)], "hi"), null);
});

test("an on-device voice for the language is chosen over an earlier cloud voice", () => {
  const voices = [v("Google हिन्दी", "hi-IN", false), v("Lekha", "hi-IN", true)];
  assert.equal(pickDeviceVoice(voices, "hi")?.name, "Lekha");
});

test("only a voice for the selected language is used", () => {
  assert.equal(pickDeviceVoice([v("Samantha", "en-US", true)], "hi"), null);
  assert.equal(pickDeviceVoice([v("Samantha", "EN-us", true)], "en")?.name, "Samantha");
});

test("no voices means unavailable", () => {
  assert.equal(pickDeviceVoice([], "or"), null);
});
