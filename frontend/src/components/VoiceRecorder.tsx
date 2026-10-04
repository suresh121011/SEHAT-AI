"use client";

import { useEffect, useRef, useState } from "react";

import { Icon } from "@/components/Icon";
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
  // False once the page is left. Stopping the tracks on unmount ends the MediaRecorder, whose `onstop`
  // would otherwise still hand the audio to `onRecorded` and upload it after the user has gone.
  const mounted = useRef(true);
  const starting = useRef(false); // a second click during the permission prompt would open a second stream

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

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      if (recorder.current?.state === "recording") recorder.current.stop();
      cleanup();
    };
  }, []);

  async function start() {
    if (starting.current || stream.current) return;
    starting.current = true;
    setError(null);
    let media: MediaStream;
    try {
      media = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
    } catch {
      setError("Microphone permission was not granted. You can use a sample clip or type instead.");
      return;
    } finally {
      starting.current = false;
    }
    if (!mounted.current) {
      media.getTracks().forEach((t) => t.stop());
      return;
    }
    stream.current = media;
    const chunks: Blob[] = [];
    let rec: MediaRecorder;
    try {
      rec = new MediaRecorder(media);
      rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
      rec.onstop = async () => {
        cleanup();
        if (!mounted.current) return; // page left while recording: discard, never upload
        setRecording(false);
        if (chunks.length === 0) {
          setError("Nothing was recorded. Please try again.");
          return;
        }
        try {
          onRecorded(await blobToWav16k(new Blob(chunks, { type: rec.mimeType })), "microphone");
        } catch {
          setError("The recording could not be converted. Please try again.");
        }
      };
      // Level meter only (AnalyserNode); nothing is sent anywhere until the user stops recording.
      audioCtx.current = new AudioContext();
      const analyser = audioCtx.current.createAnalyser();
      audioCtx.current.createMediaStreamSource(media).connect(analyser);
      const buf = new Uint8Array(analyser.fftSize);
      const draw = () => {
        analyser.getByteTimeDomainData(buf);
        let peak = 0;
        for (const v of buf) peak = Math.max(peak, Math.abs(v - 128));
        setLevel(peak / 128);
        timers.current.raf = requestAnimationFrame(draw);
      };
      draw();
      rec.start();
    } catch {
      cleanup(); // release the microphone if the recorder could not be set up
      setError("Recording could not start in this browser. You can use a sample clip or type instead.");
      return;
    }
    recorder.current = rec;
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
    // Samples are synthetic demo audio, but they are processed like a recording and stored on THIS case.
    if (!window.confirm("Add a synthetic demo clip to this case? Its values would appear as if spoken for this patient.")) return;
    setError(null);
    try {
      const res = await fetch(url);
      if (!res.ok) throw new Error(String(res.status));
      onRecorded(new Blob([await res.arrayBuffer()], { type: "audio/wav" }), "sample");
    } catch {
      setError("The sample clip could not be loaded.");
    }
  }

  return (
    <div className="space-y-3 rounded-lg border border-subtle bg-card p-4">
      <div className="flex flex-wrap items-center gap-3">
        {!recording ? (
          <button type="button" onClick={start} disabled={disabled || !micAvailable} className="inline-flex min-h-12 items-center gap-2 rounded bg-primary px-5 py-2 text-lg font-bold text-white hover:bg-primary-hover disabled:cursor-not-allowed disabled:opacity-55">
            <Icon name="mic" size={22} /> Start recording
          </button>
        ) : (
          <button type="button" onClick={stop} className="inline-flex min-h-12 items-center gap-2 rounded border-2 border-error bg-card px-5 py-2 text-lg font-bold text-error">
            <span className="inline-block size-3 rounded-sm bg-error" aria-hidden="true" /> Stop recording
          </button>
        )}
        {/* Visible timer updates every second; the screen-reader announcement only changes on start, stop and 10 s left. */}
        <span className="inline-flex items-center gap-2 font-mono text-base" aria-hidden="true">
          {recording && <span className="inline-block size-3 rounded-full bg-error" />}
          {recording ? `Recording 0:${String(elapsed).padStart(2, "0")} of 0:${maxSeconds}` : `Up to ${maxSeconds} seconds`}
        </span>
        <span className="sr-only" aria-live="polite">
          {recording ? (maxSeconds - elapsed <= 10 ? "Recording, 10 seconds or less left" : "Recording started") : "Not recording"}
        </span>
      </div>
      <div className="h-2 w-full overflow-hidden rounded bg-subtle" aria-hidden="true">
        <div className="h-full bg-primary transition-[width] motion-reduce:transition-none" style={{ width: `${Math.round(level * 100)}%` }} />
      </div>
      {!micAvailable && <p className="text-sm text-muted">The microphone needs this page to be opened on localhost or over HTTPS. You can use a sample clip.</p>}
      {samples.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span className="text-muted">Demo fallback:</span>
          {samples.map((s) => (
            <button key={s.url} type="button" disabled={disabled || recording} onClick={() => playSample(s.url)} className="rounded border border-line min-h-11 px-4 py-2 disabled:opacity-50">
              {s.label}
            </button>
          ))}
        </div>
      )}
      {error && (
        <p role="alert" className="text-sm text-error">
          {error}
        </p>
      )}
    </div>
  );
}
