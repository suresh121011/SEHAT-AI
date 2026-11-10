"use client";

import { useEffect, useState } from "react";

import { pickDeviceVoice } from "./deviceVoice.ts";

// Browser speech synthesis: only an on-device voice (never a network voice) whose language matches the selected
// language is ever used (see deviceVoice.ts). If none is installed, callers must show that spoken output is unavailable.
export function useMatchingVoice(prefix: string) {
  const [voice, setVoice] = useState<SpeechSynthesisVoice | null>(null);
  const [supported, setSupported] = useState(true);
  useEffect(() => {
    if (typeof window === "undefined" || !("speechSynthesis" in window)) {
      setSupported(false);
      return;
    }
    const pick = () => {
      const voices = window.speechSynthesis.getVoices();
      setVoice(pickDeviceVoice(voices, prefix));
    };
    pick();
    window.speechSynthesis.addEventListener("voiceschanged", pick);
    return () => window.speechSynthesis.removeEventListener("voiceschanged", pick);
  }, [prefix]);
  return { voice, supported };
}
