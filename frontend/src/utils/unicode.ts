/**
 * Converts a Python character index (Unicode code point offset) to a
 * JavaScript string index (UTF-16 code unit offset).
 *
 * In Python, len("🚀") is 1. In JavaScript, "🚀".length is 2 (surrogate pair).
 * This utility iterates through the string by Unicode code points (using Array.from)
 * up to pyOffset, and sums the UTF-16 code units.
 */
export function pyCodePointToJsUtf16Offset(text: string, pyOffset: number): number {
  if (pyOffset <= 0 || !text) return 0;
  const codePoints = Array.from(text);
  if (pyOffset >= codePoints.length) {
    return text.length;
  }
  let utf16Count = 0;
  for (let i = 0; i < pyOffset; i++) {
    const cp = codePoints[i];
    const code = cp.codePointAt(0) ?? 0;
    utf16Count += code > 0xffff ? 2 : 1;
  }
  return utf16Count;
}

/**
 * Given text and Python code point offsets (start, end),
 * returns the exact UTF-16 slice indices [jsStart, jsEnd] for use with String.prototype.slice().
 */
export function getJsOffsets(
  text: string,
  pyStart: number,
  pyEnd: number
): { jsStart: number; jsEnd: number } {
  return {
    jsStart: pyCodePointToJsUtf16Offset(text, pyStart),
    jsEnd: pyCodePointToJsUtf16Offset(text, pyEnd),
  };
}

export interface HighlightSlice {
  before: string;
  highlighted: string;
  after: string;
  isHighlighted: boolean;
}

/**
 * Slices a document for highlighting given an evidence span.
 * Returns before, highlighted, after segments and whether highlighting is active.
 * Safely handles missing, invalid, or out-of-bounds offsets.
 */
export function sliceDocumentForHighlight(
  rawText: string,
  evidence?: { start?: number | null; end?: number | null } | null
): HighlightSlice {
  if (
    !evidence ||
    typeof evidence.start !== "number" ||
    typeof evidence.end !== "number" ||
    evidence.start < 0 ||
    evidence.end <= evidence.start
  ) {
    return { before: rawText, highlighted: "", after: "", isHighlighted: false };
  }

  const { jsStart, jsEnd } = getJsOffsets(rawText, evidence.start, evidence.end);
  if (jsStart < 0 || jsEnd > rawText.length || jsStart >= jsEnd) {
    return { before: rawText, highlighted: "", after: "", isHighlighted: false };
  }

  return {
    before: rawText.slice(0, jsStart),
    highlighted: rawText.slice(jsStart, jsEnd),
    after: rawText.slice(jsEnd),
    isHighlighted: true,
  };
}
