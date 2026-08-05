import { createFileRoute } from '@tanstack/react-router'
import CommandCenterPage from '../pages/CommandCenterPage'

export const Route = createFileRoute('/command')({
  component: CommandCenterPage,
})
