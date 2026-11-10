// Device voice selection (no React, no DOM; unit-tested with `node --test`).
// "This device's voice" must stay on the device. Some browser voices are network services (Chrome's "Google …"
// voices, Edge's "… Online (Natural)" voices) that send the spoken text to Google or Microsoft, which no consent here
// covers. Only a voice the browser reports as `localService` for the selected language is ever used; otherwise spoken
// output is unavailable and the caller says so.

export type VoiceLike = { name: string; lang: string; localService: boolean };

export function pickDeviceVoice<V extends VoiceLike>(voices: readonly V[], prefix: string): V | null {
  return voices.find((v) => v.localService === true && v.lang.toLowerCase().startsWith(prefix)) ?? null;
}
