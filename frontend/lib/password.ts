/**
 * Browser-side helpers for the password checker.
 *
 * A password is hashed here, in the tab, and only its SHA-1 is sent to the
 * API — the server and leakedpassword.com never see the password itself.
 * Pwned Passwords indexes the SHA-1 of the UTF-8 bytes, which is what this
 * computes.
 */

const SHA1_RE = /^[0-9a-f]{40}$/i;

export function isSha1(value: string): boolean {
  return SHA1_RE.test(value.trim());
}

function toHex(bytes: Uint8Array): string {
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

/** Lowercase hex SHA-1 of the UTF-8 text. */
export async function sha1Hex(text: string): Promise<string> {
  const bytes = new TextEncoder().encode(text);
  // crypto.subtle only exists in a secure context. The site is HTTPS and dev
  // runs on localhost, both fine; a dev box opened over plain http:// on the
  // LAN is not, and falls back to the implementation below.
  const subtle = typeof crypto !== "undefined" ? crypto.subtle : undefined;
  if (subtle) return toHex(new Uint8Array(await subtle.digest("SHA-1", bytes)));
  return sha1Fallback(bytes);
}

/** FIPS 180-4 SHA-1, for when WebCrypto is unavailable. */
function sha1Fallback(msg: Uint8Array): string {
  const padded = Math.ceil((msg.length + 9) / 64) * 64;
  const buf = new Uint8Array(padded);
  buf.set(msg);
  buf[msg.length] = 0x80;
  const view = new DataView(buf.buffer);
  const bits = msg.length * 8;
  view.setUint32(padded - 8, Math.floor(bits / 0x100000000));
  view.setUint32(padded - 4, bits >>> 0);

  const h = [0x67452301, 0xefcdab89, 0x98badcfe, 0x10325476, 0xc3d2e1f0];
  const w = new Uint32Array(80);
  for (let off = 0; off < padded; off += 64) {
    for (let i = 0; i < 16; i++) w[i] = view.getUint32(off + i * 4);
    for (let i = 16; i < 80; i++) {
      const x = w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16];
      w[i] = (x << 1) | (x >>> 31);
    }
    let [a, b, c, d, e] = h;
    for (let i = 0; i < 80; i++) {
      let f: number, k: number;
      if (i < 20) { f = (b & c) | (~b & d); k = 0x5a827999; }
      else if (i < 40) { f = b ^ c ^ d; k = 0x6ed9eba1; }
      else if (i < 60) { f = (b & c) | (b & d) | (c & d); k = 0x8f1bbcdc; }
      else { f = b ^ c ^ d; k = 0xca62c1d6; }
      const t = (((a << 5) | (a >>> 27)) + f + e + k + w[i]) >>> 0;
      e = d; d = c; c = ((b << 30) | (b >>> 2)) >>> 0; b = a; a = t;
    }
    h[0] = (h[0] + a) >>> 0; h[1] = (h[1] + b) >>> 0; h[2] = (h[2] + c) >>> 0;
    h[3] = (h[3] + d) >>> 0; h[4] = (h[4] + e) >>> 0;
  }
  return h.map((x) => x.toString(16).padStart(8, "0")).join("");
}

/** What a password is made of, worked out locally — nothing here is sent anywhere. */
export interface Composition {
  length: number;
  lower: boolean;
  upper: boolean;
  digit: boolean;
  symbol: boolean;
  /** Letters outside ASCII, emoji, and so on. */
  other: boolean;
  /** Character-pool estimate, discounted for repeats and runs like "abc" or "321". */
  bits: number;
  rating: "very weak" | "weak" | "fair" | "strong" | "very strong";
  notes: string[];
}

export function analyze(pw: string): Composition {
  const chars = Array.from(pw);
  const lower = /[a-z]/.test(pw);
  const upper = /[A-Z]/.test(pw);
  const digit = /[0-9]/.test(pw);
  const symbol = /[\x20-\x2f\x3a-\x40\x5b-\x60\x7b-\x7e]/.test(pw);
  const other = chars.some((ch) => ch.codePointAt(0)! > 0x7e);
  const pool =
    (lower ? 26 : 0) + (upper ? 26 : 0) + (digit ? 10 : 0) + (symbol ? 33 : 0) + (other ? 100 : 0);

  // A repeated character, or one that continues a +1/-1 run, adds next to
  // nothing for an attacker: "aaaaaa" and "abcdef" are each about two guesses
  // deep, not six characters' worth.
  let effective = 0;
  let repeats = 0;
  let runs = 0;
  const codes = chars.map((ch) => ch.codePointAt(0)!);
  for (let i = 0; i < codes.length; i++) {
    if (i > 0 && codes[i] === codes[i - 1]) { repeats++; continue; }
    const step = i > 1 ? codes[i] - codes[i - 1] : 0;
    if (i > 1 && Math.abs(step) === 1 && step === codes[i - 1] - codes[i - 2]) { runs++; continue; }
    effective++;
  }
  const bits = pool ? Math.round(effective * Math.log2(pool)) : 0;

  const notes: string[] = [];
  if (chars.length < 12) notes.push("shorter than 12 characters");
  if (chars.length && !lower && !upper && !other) notes.push(digit && !symbol ? "digits only" : "no letters");
  // Only when they are a real share of it: "battery" has a double letter too.
  if (repeats >= 2 && repeats * 5 >= chars.length) notes.push("repeated characters");
  if (runs >= 2 && runs * 5 >= chars.length) notes.push("sequential characters");

  const rating =
    bits < 28 ? "very weak" : bits < 36 ? "weak" : bits < 60 ? "fair" : bits < 80 ? "strong" : "very strong";
  return { length: chars.length, lower, upper, digit, symbol, other, bits, rating, notes };
}

/** "h•••••2" — enough to tell entries apart on screen without showing them. */
export function maskEntry(s: string): string {
  const c = Array.from(s);
  if (c.length <= 2) return "•".repeat(c.length);
  return c[0] + "•".repeat(Math.min(c.length - 2, 12)) + c[c.length - 1];
}
