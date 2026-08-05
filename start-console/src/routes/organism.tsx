import { createFileRoute } from '@tanstack/react-router'
import OrganismPage from '../pages/OrganismPage'

export const Route = createFileRoute('/organism')({
  component: OrganismPage,
})
