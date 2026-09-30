"use client";

import { useEffect, useState } from "react";

// Browser speech synthesis: only a voice whose language matches the selected language is ever used.
// If none is installed on this device, callers must show that spoken output is unavailable.
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
      setVoice(voices.find((v) => v.lang.toLowerCase().startsWith(prefix)) ?? null);
    };
    pick();
    window.speechSynthesis.addEventListener("voiceschanged", pick);
    return () => window.speechSynthesis.removeEventListener("voiceschanged", pick);
  }, [prefix]);
  return { voice, supported };
}
