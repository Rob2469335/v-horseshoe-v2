# Run from the repo root — installs Vitest + RTL into organism-console
Set-Location organism-console

npm install --save-dev `
  vitest `
  @vitest/ui `
  @testing-library/react `
  @testing-library/jest-dom `
  @testing-library/user-event `
  jsdom

# Add test scripts (or edit package.json manually):
# "test":       "vitest run"
# "test:watch": "vitest"
# "test:ui":    "vitest --ui"
npm pkg set scripts.test="vitest run"
npm pkg set scripts.test:watch="vitest"
npm pkg set scripts.test:ui="vitest --ui"

Write-Host "Done. Run: npm test" -ForegroundColor Green
