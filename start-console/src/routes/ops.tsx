import { createFileRoute } from '@tanstack/react-router'
import OpsPage from '../pages/OpsPage'

export const Route = createFileRoute('/ops')({
  component: OpsPage,
})
