"use client";

import * as React from "react";
import { isAndroidApp } from "@/lib/platform";

/** Tags <html> with `in-app` inside the Android app. See lib/platform.ts. */
export function InAppMarker() {
  React.useEffect(() => {
    if (isAndroidApp()) document.documentElement.classList.add("in-app");
  }, []);
  return null;
}
