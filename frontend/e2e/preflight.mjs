import { existsSync } from "node:fs";
import { chromium } from "playwright";

const channel =
  process.env.SHOPLENS_BROWSER_CHANNEL ??
  (process.platform === "win32" ? "msedge" : "chromium");
const candidates =
  channel === "msedge"
    ? [
        `${process.env["PROGRAMFILES(X86)"]}/Microsoft/Edge/Application/msedge.exe`,
        `${process.env.PROGRAMFILES}/Microsoft/Edge/Application/msedge.exe`,
        `${process.env.LOCALAPPDATA}/Microsoft/Edge/Application/msedge.exe`,
      ]
    : channel === "chromium"
      ? [chromium.executablePath()]
      : null;
if (candidates && !candidates.some(existsSync)) {
  console.log(
    `NOT_RUN: ${channel} browser is not installed. See README browser setup.`,
  );
  process.exit(2);
}
