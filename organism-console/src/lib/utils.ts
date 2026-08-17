import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

// Scheme allowlist for user/backend-supplied external links. React escapes
// attribute VALUES but not href schemes — a `javascript:`/`data:` URL in an
// href executes on click. Allow only http(s) (external pages) and mailto
// (unsubscribe links). Returns undefined for anything else so the caller can
// omit the href (renders as non-clickable text) instead of shipping a
// script-executing link.
export function safeExternalUrl(raw: unknown): string | undefined {
  if (typeof raw !== "string") return undefined
  const trimmed = raw.trim()
  if (!trimmed) return undefined
  const lower = trimmed.toLowerCase()
  if (lower.startsWith("http://") || lower.startsWith("https://")) return trimmed
  if (lower.startsWith("mailto:")) {
    // mailto: must carry a bare email address, not a javascript: payload that
    // slipped past a naive `mailto:` prefix strip.
    const target = trimmed.slice("mailto:".length).trim()
    if (!target || target.length > 320) return undefined
    // Rough email shape (local@domain.tld); anything else (javascript:...,
    // spaces, fragments) is dropped, not linked.
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(target)) return undefined
    return `mailto:${target}`
  }
  return undefined
}
