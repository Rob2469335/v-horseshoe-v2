import { createFileRoute } from '@tanstack/react-router'
import MemorySearchPage from '../pages/MemorySearchPage'

export const Route = createFileRoute('/memory-search')({
  component: MemorySearchPage,
})
