import { createFileRoute } from '@tanstack/react-router'
import LearnedMemoriesPage from '../pages/LearnedMemoriesPage'

export const Route = createFileRoute('/memories')({
  component: LearnedMemoriesPage,
})
