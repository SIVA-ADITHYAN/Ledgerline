import test from "node:test";
import assert from "node:assert";

// Implementation of pyCodePointToJsUtf16Offset to test
function pyCodePointToJsUtf16Offset(text, pyOffset) {
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

function getJsOffsets(text, pyStart, pyEnd) {
  return {
    jsStart: pyCodePointToJsUtf16Offset(text, pyStart),
    jsEnd: pyCodePointToJsUtf16Offset(text, pyEnd),
  };
}

test("unicode - ascii text has identical offsets", () => {
  const text = "Invoice SIS/26-27/0042 Total: 23,458.00";
  // Python start=8, end=22 is "SIS/26-27/0042"
  const { jsStart, jsEnd } = getJsOffsets(text, 8, 22);
  assert.strictEqual(jsStart, 8);
  assert.strictEqual(jsEnd, 22);
  assert.strictEqual(text.slice(jsStart, jsEnd), "SIS/26-27/0042");
});

test("unicode - bmp characters like rupee symbol count as 1 code unit in both", () => {
  const text = "Total ₹ 23,458.00";
  // ₹ is U+20B9, within BMP (code point <= 0xFFFF), 1 UTF-16 code unit
  // Python start=8, end=17 is "23,458.00"
  const { jsStart, jsEnd } = getJsOffsets(text, 8, 17);
  assert.strictEqual(jsStart, 8);
  assert.strictEqual(jsEnd, 17);
  assert.strictEqual(text.slice(jsStart, jsEnd), "23,458.00");
});

test("unicode - non-bmp characters (astral/emoji) properly shift utf-16 offsets", () => {
  // 📦 (U+1F4E6), ✨ (U+2728 in BMP), 🚀 (U+1F680)
  // 📦 is non-BMP (2 UTF-16 code units, 1 Python code point)
  // 🚀 is non-BMP (2 UTF-16 code units, 1 Python code point)
  const text = "📦 Logistics\n🚀 TAX INVOICE\nInvoice No: SIS/26-27/0042\nTotal: 100.00";

  // In Python code points:
  // "📦 Logistics\n" -> 1 + 11 = 12
  // "🚀 TAX INVOICE\n" -> 1 + 13 = 14 (total 26)
  // "Invoice No: " -> 12 (total 38)
  // "SIS/26-27/0042" -> 14 code points -> pyStart = 38, pyEnd = 52.
  const codePoints = Array.from(text);
  const targetCodePoints = Array.from("SIS/26-27/0042");
  const pyStart = codePoints.findIndex((_, idx) =>
    targetCodePoints.every((ch, j) => codePoints[idx + j] === ch)
  );
  const pyEnd = pyStart + targetCodePoints.length;

  assert.strictEqual(pyStart, 38);
  assert.strictEqual(pyEnd, 52);

  const { jsStart, jsEnd } = getJsOffsets(text, pyStart, pyEnd);

  // In JS UTF-16, two non-BMP emojis (📦 and 🚀) add 1 extra code unit each (+2).
  assert.strictEqual(jsStart, 40);
  assert.strictEqual(jsEnd, 54);

  // Exact slice matches!
  assert.strictEqual(text.slice(jsStart, jsEnd), "SIS/26-27/0042");
});

