import { createFileRoute } from '@tanstack/react-router'
import AgentPage from '../pages/AgentPage'

export const Route = createFileRoute('/')({
  component: AgentPage,
})
