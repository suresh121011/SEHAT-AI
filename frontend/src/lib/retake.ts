// Plain-language advice for a document the quality check rejected (backend app/ocr/quality.py reasons).
// Shown when the upload is refused AND kept on the rejected document's card, so the reason is never lost.

const ADVICE: Record<string, string> = {
  text_too_small: "The text is too small to read reliably — upload a higher-resolution copy (the original file, or a closer photo where the page fills the picture).",
  blurry: "The image is blurry — hold the phone steady, tap to focus, and retake it.",
  skewed: "The page is tilted — place it flat and photograph it straight on.",
  too_dark: "The photo is too dark — retake it in better light.",
  overexposed: "The photo is washed out — avoid direct light or flash glare on the page.",
  low_contrast: "The text is too faint against the page — retake it in even light.",
  blank_page: "No text was found on the page — check that the right side of the page was photographed.",
};

export function retakeAdvice(reasons: readonly string[]): string[] {
  const out = [...new Set(reasons)].map((r) => ADVICE[r] ?? `The document could not be read (${r.replaceAll("_", " ")}) — please retake it.`);
  return out.length ? out : ["The document could not be read — please retake it: flat page, good light, in focus."];
}
