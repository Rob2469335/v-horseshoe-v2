export function formatList(items: string[] | undefined) {
  if (!items || items.length === 0) return "None"
  return items.join(", ")
}

export function formatBoolean(value: boolean | undefined) {
  return value ? "Yes" : "No"
}

export function getErrorMessage(error: unknown) {
  return error instanceof Error ? error.message : String(error)
}

export function hasCapability(capabilities: string[] | undefined, name: string) {
  return (capabilities ?? []).some((item) => item.toLowerCase() === name.toLowerCase())
}

export function getTimelineUrl(backendUrl: string) {
  return `${backendUrl.replace(/\/$/, "")}/timeline?window_minutes=20000`
}

export function formatCompact(value: number) {
  return new Intl.NumberFormat("en-US", {
    notation: "compact",
    maximumFractionDigits: 1
  }).format(value)
}

export function getLinePoints(values: number[], width: number, height: number, padding: number) {
  if (!values.length) return ""

  const maxY = Math.max(...values, 1)
  const innerWidth = width - padding * 2
  const innerHeight = height - padding * 2
  const stepX = values.length > 1 ? innerWidth / (values.length - 1) : innerWidth / 2

  return values
    .map((value, index) => {
      const x = padding + index * stepX
      const y = padding + innerHeight - (value / maxY) * innerHeight
      return `${x},${y}`
    })
    .join(" ")
}

export function getAreaPoints(values: number[], width: number, height: number, padding: number) {
  const line = getLinePoints(values, width, height, padding)
  if (!line || !values.length) return ""
  return `${padding},${height - padding} ${line} ${width - padding},${height - padding}`
}

export function getStatusColor(value: boolean | undefined) {
  return value ? "#4ade80" : "#f59e0b"
}

export function getStatusText(value: boolean | undefined, positive: string, negative: string) {
  return value ? positive : negative
}
