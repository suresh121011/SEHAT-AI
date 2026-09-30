"use client";

import { useEffect, useRef, useState } from "react";

import { blobToWav16k } from "@/lib/wav";

// Records from the microphone and hands back a 16 kHz mono WAV. Browser speech *recognition* is not
// used (it sends audio to a third party). The microphone only works on localhost or HTTPS.
// A browser microphone permission is not application consent: the page checks consent first.

type Props = {
  maxSeconds: number;
  disabled?: boolean;
  onRecorded: (wav: Blob, source: "microphone" | "sample") => void;
  samples?: { label: string; url: string }[];
};

export function VoiceRecorder({ maxSeconds, disabled, onRecorded, samples = [] }: Props) {
  const [recording, setRecording] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [level, setLevel] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const timers = useRef<{ tick?: number; stop?: number; raf?: number }>({});
  const audioCtx = useRef<AudioContext | null>(null);

  // Decided after mount: the page is prerendered without `window`, so computing this during render
  // would make server and client markup differ.
  const [micAvailable, setMicAvailable] = useState(true);
  useEffect(() => setMicAvailable(window.isSecureContext && !!navigator.mediaDevices?.getUserMedia), []);

  function cleanup() {
    window.clearInterval(timers.current.tick);
    window.clearTimeout(timers.current.stop);
    if (timers.current.raf) cancelAnimationFrame(timers.current.raf);
    stream.current?.getTracks().forEach((t) => t.stop());
    stream.current = null;
    audioCtx.current?.close().catch(() => undefined);
    audioCtx.current = null;
    setLevel(0);
  }

  useEffect(() => cleanup, []);

  async function start() {
    setError(null);
    try {
      stream.current = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
    } catch {
      setError("Microphone permission was not granted. You can use a sample clip or type instead.");
      return;
    }
    const chunks: Blob[] = [];
    const rec = new MediaRecorder(stream.current);
    rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    rec.onstop = async () => {
      cleanup();
      setRecording(false);
      try {
        onRecorded(await blobToWav16k(new Blob(chunks, { type: rec.mimeType })), "microphone");
      } catch {
        setError("The recording could not be converted. Please try again.");
      }
    };
    // Level meter only (AnalyserNode); nothing is sent anywhere until the user stops recording.
    audioCtx.current = new AudioContext();
    const analyser = audioCtx.current.createAnalyser();
    audioCtx.current.createMediaStreamSource(stream.current).connect(analyser);
    const buf = new Uint8Array(analyser.fftSize);
    const draw = () => {
      analyser.getByteTimeDomainData(buf);
      let peak = 0;
      for (const v of buf) peak = Math.max(peak, Math.abs(v - 128));
      setLevel(peak / 128);
      timers.current.raf = requestAnimationFrame(draw);
    };
    draw();
    recorder.current = rec;
    rec.start();
    setRecording(true);
    setElapsed(0);
    const began = Date.now();
    timers.current.tick = window.setInterval(() => setElapsed(Math.floor((Date.now() - began) / 1000)), 250);
    timers.current.stop = window.setTimeout(() => rec.state === "recording" && rec.stop(), maxSeconds * 1000 - 250);
  }

  function stop() {
    if (recorder.current?.state === "recording") recorder.current.stop();
  }

  async function playSample(url: string) {
    setError(null);
    const res = await fetch(url);
    onRecorded(new Blob([await res.arrayBuffer()], { type: "audio/wav" }), "sample");
  }

  return (
    <div className="space-y-3 rounded border border-black/10 p-4 dark:border-white/15">
      <div className="flex flex-wrap items-center gap-3">
        {!recording ? (
          <button type="button" onClick={start} disabled={disabled || !micAvailable} className="rounded bg-red-600 px-4 py-2 font-medium text-white disabled:opacity-50">
            🎤 Start recording
          </button>
        ) : (
          <button type="button" onClick={stop} className="rounded bg-black px-4 py-2 font-medium text-white dark:bg-white dark:text-black">
            ■ Stop
          </button>
        )}
        <span aria-live="polite" className="font-mono text-sm">
          {recording ? `🔴 Recording… 0:${String(elapsed).padStart(2, "0")} / 0:${maxSeconds}` : `Up to ${maxSeconds} seconds`}
        </span>
      </div>
      <div className="h-2 w-full overflow-hidden rounded bg-black/10 dark:bg-white/10" aria-hidden="true">
        <div className="h-full bg-green-600 transition-[width]" style={{ width: `${Math.round(level * 100)}%` }} />
      </div>
      {!micAvailable && <p className="text-sm opacity-80">The microphone needs this page to be opened on localhost or over HTTPS. You can use a sample clip.</p>}
      {samples.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span className="opacity-70">Demo fallback:</span>
          {samples.map((s) => (
            <button key={s.url} type="button" disabled={disabled || recording} onClick={() => playSample(s.url)} className="rounded border border-black/20 px-3 py-1 disabled:opacity-50 dark:border-white/20">
              ▶ {s.label}
            </button>
          ))}
        </div>
      )}
      {error && (
        <p role="alert" className="text-sm text-red-600">
          {error}
        </p>
      )}
    </div>
  );
}
