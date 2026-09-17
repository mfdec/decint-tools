import * as React from "react";
import { Shield } from "@/components/icons";

/**
 * The DECINT lockup: empty violet shield + tracked wordmark.
 *
 * Defined once and used by the nav, the footer and the auth pages, so the
 * relationship between mark and word — gap, optical size, tracking — is stated
 * in exactly one place. Previously each surface re-declared its own spacing and
 * they had already drifted apart by 2px and one weight step.
 *
 * The shield renders a touch larger than the cap height on purpose: an outline
 * mark next to solid letterforms reads visually smaller than it measures, so
 * matching them numerically makes the shield look shrunken.
 */
export function Wordmark({
  size = "md",
  muted = false,
}: {
  size?: "sm" | "md";
  muted?: boolean;
}) {
  const s = size === "sm" ? { icon: 15, text: 13, gap: 9 } : { icon: 20, text: 16, gap: 10 };

  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: s.gap }}>
      <Shield
        size={s.icon}
        style={{ color: "var(--color-accent)", flex: "none", display: "block" }}
      />
      <span
        className="wordmark"
        style={{
          fontSize: s.text,
          color: muted ? "var(--color-neutral-400)" : "var(--color-text)",
          lineHeight: 1,
        }}
      >
        DECINT
      </span>
    </span>
  );
}
