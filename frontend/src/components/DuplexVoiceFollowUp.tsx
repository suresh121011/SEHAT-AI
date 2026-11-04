"use client";

import { useEffect, useRef, useState } from "react";
import { Icon } from "@/components/Icon";
import { Button, Card, Notice, Spinner } from "@/components/ui";

type Props = {
  caseId: string;
  onFieldsUpdated?: (fields: LiveField[]) => void;
  onConcluded?: () => void;
};

export type LiveField = {
  field_id?: string;
  field_name: string;
  value: string;
  unit?: string | null;
  patient_phrase?: string | null;
  status: string;
  source: string;
};

type SessionState = "idle" | "connecting" | "active" | "speaking" | "concluded" | "error";

const CANONICAL_LABELS: Record<string, string> = {
  chief_complaint: "Chief Complaint",
  duration: "Symptom Duration",
  pain_severity: "Pain Severity",
  spo2: "Oxygen Saturation (SpO2)",
  bp: "Blood Pressure",
  pulse: "Pulse Rate",
  resp_rate: "Respiratory Rate",
  temp: "Body Temperature",
  danger_signs: "Danger Sign Screening",
  lmp: "Last Menstrual Period (LMP)",
  edd: "Estimated Delivery Date (EDD)",
  gravida_parity: "Gravida / Parity",
  hb: "Haemoglobin (Hb)",
};

export function DuplexVoiceFollowUp({ caseId, onFieldsUpdated, onConcluded }: Props) {
  const [state, setState] = useState<SessionState>("idle");
  const [language, setLanguage] = useState<"hi" | "or">("or");
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [capturedFields, setCapturedFields] = useState<LiveField[]>([]);
  const [assistantSpeaking, setAssistantSpeaking] = useState(false);
  const [micLevel, setMicLevel] = useState<number>(0);
  const [finishReason, setFinishReason] = useState<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const micStreamRef = useRef<MediaStream | null>(null);
  const micCtxRef = useRef<AudioContext | null>(null);
  const playbackCtxRef = useRef<AudioContext | null>(null);
  const nextPlayTimeRef = useRef<number>(0);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      cleanup();
    };
  }, []);

  function cleanup() {
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
    if (micStreamRef.current) {
      micStreamRef.current.getTracks().forEach((t) => t.stop());
      micStreamRef.current = null;
    }
    if (micCtxRef.current) {
      micCtxRef.current.close().catch(() => undefined);
      micCtxRef.current = null;
    }
    if (playbackCtxRef.current) {
      playbackCtxRef.current.close().catch(() => undefined);
      playbackCtxRef.current = null;
    }
    setAssistantSpeaking(false);
    setMicLevel(0);
  }

  // Play incoming 24kHz PCM linear16 chunks
  function playAudioChunk(pcmBase64: string) {
    try {
      if (!playbackCtxRef.current || playbackCtxRef.current.state === "closed") {
        playbackCtxRef.current = new (window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext)({
          sampleRate: 24000,
        });
        nextPlayTimeRef.current = playbackCtxRef.current.currentTime;
      }

      const binaryStr = atob(pcmBase64);
      const len = binaryStr.length;
      const bytes = new Uint8Array(len);
      for (let i = 0; i < len; i++) {
        bytes[i] = binaryStr.charCodeAt(i);
      }

      const int16 = new Int16Array(bytes.buffer);
      const float32 = new Float32Array(int16.length);
      for (let i = 0; i < int16.length; i++) {
        float32[i] = int16[i] / 32768.0;
      }

      const ctx = playbackCtxRef.current;
      const audioBuffer = ctx.createBuffer(1, float32.length, 24000);
      audioBuffer.getChannelData(0).set(float32);

      const sourceNode = ctx.createBufferSource();
      sourceNode.buffer = audioBuffer;
      sourceNode.connect(ctx.destination);

      const startTime = Math.max(ctx.currentTime, nextPlayTimeRef.current);
      sourceNode.start(startTime);
      nextPlayTimeRef.current = startTime + audioBuffer.duration;

      setAssistantSpeaking(true);
      sourceNode.onended = () => {
        if (ctx.currentTime >= nextPlayTimeRef.current - 0.05) {
          setAssistantSpeaking(false);
        }
      };
    } catch (err) {
      console.warn("Failed to decode and play audio chunk:", err);
    }
  }

  async function startDuplexSession() {
    cleanup();
    setErrorMsg(null);
    setState("connecting");

    try {
      // 1. Fetch auth token
      const sessionRes = await fetch("/api/session/token");
      if (!sessionRes.ok) {
        throw new Error("Unable to retrieve session token. Please re-login.");
      }
      const { token } = await sessionRes.json();

      // 2. Open Mic Stream
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          sampleRate: 16000,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      micStreamRef.current = stream;

      // 3. Connect to FastAPI WebSocket
      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      const host = window.location.hostname;
      const port = window.location.port ? `:${window.location.port}` : "";
      // In dev, Next runs on 3000 and FastAPI on 8000; in proxy or direct, connect to backend URL
      const wsUrl = `${protocol}//${host}:8000/api/v1/cases/${caseId}/voice/live-followup?token=${token}&language=${language}`;

      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        if (!mountedRef.current) return;
        setState("active");

        // 4. Set up Web Audio API 16kHz PCM capture
        const micCtx = new (window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext)({
          sampleRate: 16000,
        });
        micCtxRef.current = micCtx;

        const micSource = micCtx.createMediaStreamSource(stream);
        const scriptNode = micCtx.createScriptProcessor(2048, 1, 1);

        scriptNode.onaudioprocess = (e) => {
          if (ws.readyState !== WebSocket.OPEN) return;
          const inputData = e.inputBuffer.getChannelData(0);

          // Compute audio level for UI visualizer
          let sumSquares = 0;
          const int16 = new Int16Array(inputData.length);
          for (let i = 0; i < inputData.length; i++) {
            const s = Math.max(-1, Math.min(1, inputData[i]));
            int16[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
            sumSquares += s * s;
          }
          const rms = Math.sqrt(sumSquares / inputData.length);
          setMicLevel(Math.min(1, rms * 5));

          // Stream raw 16kHz PCM to Gemini via WebSocket
          ws.send(int16.buffer);
        };

        micSource.connect(scriptNode);
        scriptNode.connect(micCtx.destination);
      };

      ws.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data);
          if (payload.event === "audio" && payload.data) {
            playAudioChunk(payload.data);
          } else if (payload.event === "field_submitted") {
            const newField: LiveField = {
              field_id: payload.field_id,
              field_name: payload.field_name,
              value: payload.value,
              unit: payload.unit,
              patient_phrase: payload.patient_phrase,
              status: payload.status || "pending_review",
              source: payload.source || "gemini_live_duplex",
            };
            setCapturedFields((prev) => {
              const updated = [...prev.filter((f) => f.field_name !== newField.field_name), newField];
              onFieldsUpdated?.(updated);
              return updated;
            });
          } else if (payload.event === "intake_finished") {
            setState("concluded");
            setFinishReason(payload.reason || "all_fields_collected");
            onConcluded?.();
            cleanup();
          } else if (payload.event === "error") {
            setErrorMsg(payload.message || "An error occurred during spoken follow-up.");
          }
        } catch (e) {
          console.warn("Failed to parse websocket message:", e);
        }
      };

      ws.onerror = () => {
        setErrorMsg("WebSocket connection to voice service failed. Please check network/consent.");
        setState("error");
        cleanup();
      };

      ws.onclose = (event) => {
        if (event.code === 4403) {
          setErrorMsg("Voice Cloud Consent is required for real-time spoken follow-up.");
        } else if (event.code === 4404) {
          setErrorMsg("Duplex Voice Follow-up is currently disabled on this server.");
        }
        if (state !== "concluded") {
          setState((s) => (s === "connecting" || s === "active" ? "idle" : s));
        }
      };
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : "Failed to start duplex voice interview.");
      setState("error");
      cleanup();
    }
  }

  function stopDuplexSession() {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: "stop" }));
    }
    setState("idle");
    cleanup();
  }

  return (
    <Card>
      <div className="flex flex-col gap-4">
        {/* Header & Language Selector */}
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border pb-3">
          <div>
            <h3 className="text-base font-semibold text-text flex items-center gap-2">
              <span className="flex h-2.5 w-2.5 rounded-full bg-emerald-500 animate-pulse" />
              Live Duplex Spoken Follow-Up (Gemini 2.0 Live)
            </h3>
            <p className="text-xs text-text-muted mt-0.5">
              The assistant asks missing questions in real time. Spoken answers automatically populate the form.
            </p>
          </div>

          <div className="flex items-center gap-2">
            <span className="text-xs font-medium text-text-muted">Language:</span>
            <div className="inline-flex rounded-lg border border-border p-0.5 bg-canvas-muted">
              <button
                type="button"
                disabled={state === "active"}
                onClick={() => setLanguage("or")}
                className={`px-3 py-1 text-xs font-semibold rounded-md transition-all ${
                  language === "or" ? "bg-primary text-white shadow-sm" : "text-text-muted hover:text-text"
                }`}
              >
                ଓଡ଼ିଆ (Odia)
              </button>
              <button
                type="button"
                disabled={state === "active"}
                onClick={() => setLanguage("hi")}
                className={`px-3 py-1 text-xs font-semibold rounded-md transition-all ${
                  language === "hi" ? "bg-primary text-white shadow-sm" : "text-text-muted hover:text-text"
                }`}
              >
                हिन्दी (Hindi)
              </button>
            </div>
          </div>
        </div>

        {errorMsg && (
          <Notice tone="error" role="alert">
            {errorMsg}
          </Notice>
        )}

        {/* Visual Duplex Indicator Orb */}
        <div className="flex flex-col items-center justify-center p-6 bg-gradient-to-b from-canvas to-canvas-muted rounded-xl border border-border">
          <div className="relative flex items-center justify-center my-4">
            {/* Outer pulsating rings */}
            {state === "active" && (
              <>
                <div
                  className={`absolute rounded-full transition-all duration-300 ${
                    assistantSpeaking
                      ? "h-28 w-28 bg-indigo-500/20 animate-ping"
                      : "h-28 w-28 bg-emerald-500/20 animate-pulse"
                  }`}
                  style={{ transform: `scale(${1 + micLevel * 0.4})` }}
                />
                <div
                  className={`absolute rounded-full transition-all duration-150 ${
                    assistantSpeaking ? "h-24 w-24 bg-indigo-400/30" : "h-24 w-24 bg-emerald-400/30"
                  }`}
                  style={{ transform: `scale(${1 + micLevel * 0.25})` }}
                />
              </>
            )}

            {/* Central Orb */}
            <div
              className={`flex h-20 w-20 items-center justify-center rounded-full shadow-lg transition-all duration-300 ${
                state === "connecting"
                  ? "bg-amber-500 text-white animate-pulse"
                  : state === "active"
                    ? assistantSpeaking
                      ? "bg-indigo-600 text-white shadow-indigo-500/50"
                      : "bg-emerald-600 text-white shadow-emerald-500/50"
                    : state === "concluded"
                      ? "bg-primary text-white"
                      : "bg-canvas-subtle text-text-muted border-2 border-border"
              }`}
            >
              {state === "connecting" ? (
                <Spinner label="Connecting to Gemini Live…" />
              ) : state === "active" ? (
                assistantSpeaking ? (
                  <Icon name="volume" className="h-8 w-8 animate-bounce" />
                ) : (
                  <Icon name="mic" className="h-8 w-8 animate-pulse" />
                )
              ) : state === "concluded" ? (
                <Icon name="check" className="h-8 w-8" />
              ) : (
                <Icon name="mic" className="h-8 w-8" />
              )}
            </div>
          </div>

          {/* Status Label */}
          <div className="text-center mt-2">
            <span className="text-sm font-semibold text-text">
              {state === "idle" && "Ready to start spoken interview"}
              {state === "connecting" && "Establishing bidirectional live session…"}
              {state === "active" && assistantSpeaking && (
                <span className="text-indigo-600 dark:text-indigo-400 font-bold">
                  Assistant Speaking ({language === "or" ? "Odia" : "Hindi"})…
                </span>
              )}
              {state === "active" && !assistantSpeaking && (
                <span className="text-emerald-600 dark:text-emerald-400 font-bold">
                  Listening to Patient… Speak clearly
                </span>
              )}
              {state === "concluded" && "Follow-up session completed!"}
              {state === "error" && "Session ended with an error"}
            </span>
            <p className="text-xs text-text-muted mt-1 max-w-sm">
              {state === "active"
                ? "Answers are transcribed and medical values are auto-filled in real time."
                : "Non-diagnostic clinical intake. All voice entries require human health worker sign-off."}
            </p>
          </div>

          {/* Action Buttons */}
          <div className="mt-5 flex flex-wrap gap-3">
            {state === "idle" || state === "error" ? (
              <Button onClick={startDuplexSession}>
                <Icon name="mic" className="h-4 w-4 mr-2" />
                Start Spoken Follow-Up ({language === "or" ? "ଓଡ଼ିଆ" : "हिन्दी"})
              </Button>
            ) : state === "active" ? (
              <Button variant="danger" onClick={stopDuplexSession}>
                Stop Interview
              </Button>
            ) : state === "concluded" ? (
              <Button onClick={startDuplexSession} variant="secondary">
                Restart Spoken Session
              </Button>
            ) : null}
          </div>
        </div>

        {/* Real-Time Auto-Filled Fields Table */}
        <div className="mt-2 space-y-2">
          <div className="flex items-center justify-between">
            <h4 className="text-sm font-semibold text-text flex items-center gap-2">
              <Icon name="document" className="h-4 w-4 text-primary" />
              Auto-Populated Information ({capturedFields.length})
            </h4>
            {capturedFields.length > 0 && (
              <span className="text-xs text-text-muted">Auto-saved to Case Record</span>
            )}
          </div>

          {capturedFields.length === 0 ? (
            <div className="rounded-lg border border-dashed border-border p-4 text-center text-xs text-text-muted">
              No fields auto-filled yet. Speak your symptoms or vitals to see them appear here.
            </div>
          ) : (
            <div className="overflow-hidden rounded-lg border border-border divide-y divide-border bg-canvas">
              {capturedFields.map((field) => (
                <div
                  key={field.field_name}
                  className="flex flex-wrap items-center justify-between p-3 gap-2 hover:bg-canvas-muted transition-colors animate-in fade-in duration-300"
                >
                  <div className="space-y-0.5">
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-semibold text-text">
                        {CANONICAL_LABELS[field.field_name] || field.field_name}
                      </span>
                      <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300">
                        Pending Review
                      </span>
                    </div>
                    {field.patient_phrase && (
                      <p className="text-[11px] text-text-muted italic">
                        Patient phrase: &ldquo;{field.patient_phrase}&rdquo;
                      </p>
                    )}
                  </div>

                  <div className="flex items-center gap-2">
                    <span className="text-sm font-bold text-primary font-mono">
                      {field.value}
                      {field.unit ? ` ${field.unit}` : ""}
                    </span>
                    <span className="inline-flex items-center px-2 py-0.5 rounded text-[10px] font-medium bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300">
                      Duplex Voice
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </Card>
  );
}

