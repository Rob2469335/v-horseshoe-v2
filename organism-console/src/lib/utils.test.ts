import { describe, it, expect } from "vitest";
import { safeExternalUrl } from "./utils";

describe("safeExternalUrl", () => {
  it("allows http and https URLs", () => {
    expect(safeExternalUrl("https://example.com/page")).toBe("https://example.com/page");
    expect(safeExternalUrl("http://example.com")).toBe("http://example.com");
  });

  it("allows plain mailto addresses", () => {
    expect(safeExternalUrl("mailto:user@example.com")).toBe("mailto:user@example.com");
  });

  it("blocks javascript: and data: schemes", () => {
    expect(safeExternalUrl("javascript:alert(1)")).toBeUndefined();
    expect(safeExternalUrl("data:text/html,<script>alert(1)</script>")).toBeUndefined();
  });

  it("blocks mailto with an embedded javascript payload", () => {
    // The backend strips the mailto: prefix, so 'mailto:javascript:alert(1)'
    // would previously surface as href='javascript:alert(1)'. Must be dropped.
    expect(safeExternalUrl("mailto:javascript:alert(1)")).toBeUndefined();
  });

  it("blocks whitespace in mailto targets", () => {
    expect(safeExternalUrl("mailto:user@example.com x")).toBeUndefined();
  });

  it("returns undefined for non-strings and empties", () => {
    expect(safeExternalUrl(undefined)).toBeUndefined();
    expect(safeExternalUrl(null)).toBeUndefined();
    expect(safeExternalUrl("")).toBeUndefined();
    expect(safeExternalUrl(42)).toBeUndefined();
  });
});
