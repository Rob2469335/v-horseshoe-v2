import { createFileRoute } from '@tanstack/react-router'
import type {} from '@tanstack/start-client-core'
import { streamText, convertToModelMessages, tool, toUIMessageStream, createUIMessageStreamResponse } from 'ai'
import { createOpenAI } from '@ai-sdk/openai'
import { z } from 'zod'

const LOCAL_LLM_BASE_URL = process.env.LOCAL_LLM_BASE_URL ?? 'http://localhost:8080/v1'
const LOCAL_LLM_API_KEY = process.env.LOCAL_LLM_API_KEY ?? 'llama'
const BACKEND_URL = process.env.ZENITH_BACKEND_URL ?? 'http://127.0.0.1:8000'

const localLLM = createOpenAI({
  baseURL: LOCAL_LLM_BASE_URL,
  apiKey: LOCAL_LLM_API_KEY,
})

const tools = {
  getSystemHealth: tool({
    description: 'Get the health status of the Zenith Swarm OS',
    inputSchema: z.object({
      module: z.enum(['frontend', 'backend', 'database']),
    }),
    execute: async ({ module }) => {
      if (module === 'backend') {
        try {
          const start = Date.now();
          const res = await fetch(`${BACKEND_URL}/readyz`);
          if (!res.ok) throw new Error(`Backend responded with ${res.status}`);
          const data = await res.json();
          const latency = Date.now() - start;
          return {
            status: (data.status === 'ready' || data.status === 'ok') ? 'healthy' : 'degraded',
            latency: `${latency}ms`,
            uptime: data.uptime || '99.9%'
          };
        } catch (e) {
          return { status: 'offline', latency: 'timeout', uptime: '0%' };
        }
      }
      
      // Simulate other modules
      await new Promise((resolve) => setTimeout(resolve, 300))
      const statuses = {
        frontend: { status: 'healthy', latency: '12ms', uptime: '100%' },
        database: { status: 'healthy', latency: '4ms', uptime: '99.9%' },
      }
      return statuses[module]
    },
  }),
}

export const Route = createFileRoute('/api/chat')({
  server: {
    handlers: {
      POST: async ({ request }) => {
        try {
          const { messages } = await request.json()

          const result = streamText({
            model: localLLM('qwen3.5-9b'),
            messages: await convertToModelMessages(messages),
            instructions: 'You are Zenith Swarm OS. You are a biological intelligence interface. You must use the provided tools to fetch system statuses or data when asked.',
            tools,
          })

          return createUIMessageStreamResponse({
            stream: toUIMessageStream({ stream: result.stream, tools }),
          })
        } catch (error) {
          console.error('Chat API Error:', error)
          return new Response(JSON.stringify({ error: 'Failed to process chat request' }), {
            status: 500,
            headers: { 'Content-Type': 'application/json' },
          })
        }
      },
    },
  },
})
