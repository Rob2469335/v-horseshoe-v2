import { HeadContent, Scripts, createRootRoute } from '@tanstack/react-router'
import { TanStackRouterDevtoolsPanel } from '@tanstack/react-router-devtools'
import { TanStackDevtools } from '@tanstack/react-devtools'
import Sidebar from '../shell/Sidebar'
import Topbar from '../shell/Topbar'
import { SwarmTopology3D } from '../components/SwarmTopology3D'
import type { ReactNode } from 'react'

import appCss from '../styles.css?url'

const THEME_INIT_SCRIPT = `(function(){try{var stored=window.localStorage.getItem('theme');var mode=(stored==='light'||stored==='dark'||stored==='auto')?stored:'auto';var prefersDark=window.matchMedia('(prefers-color-scheme: dark)').matches;var resolved=mode==='auto'?(prefersDark?'dark':'light'):mode;var root=document.documentElement;root.classList.remove('light','dark');root.classList.add(resolved);if(mode==='auto'){root.removeAttribute('data-theme')}else{root.setAttribute('data-theme',mode)}root.style.colorScheme=resolved;}catch(e){}})();`

export const Route = createRootRoute({
  component: RootDocument,
  errorComponent: ({ error }) => (
    <div className="flex items-center justify-center min-h-screen bg-black text-white p-4">
      <div className="max-w-md w-full p-6 bg-red-950/20 border border-red-500/30 rounded-2xl shadow-[0_0_30px_rgba(239,68,68,0.15)]">
        <h2 className="text-xl font-bold text-red-500 mb-2">Critical System Failure</h2>
        <p className="text-red-200/70 text-sm mb-4 font-mono break-all">{error.message}</p>
        <button 
          onClick={() => window.location.reload()}
          className="px-4 py-2 bg-red-500/10 hover:bg-red-500/20 border border-red-500/30 rounded-lg text-red-400 font-bold transition-colors"
        >
          Reboot System
        </button>
      </div>
    </div>
  ),
  pendingComponent: () => (
    <div className="flex items-center justify-center min-h-screen bg-black text-white">
      <div className="animate-pulse text-cyan-400 font-mono tracking-widest uppercase">Initializing Swarm Matrix...</div>
    </div>
  ),
  head: () => ({
    meta: [
      {
        charSet: 'utf-8',
      },
      {
        name: 'viewport',
        content: 'width=device-width, initial-scale=1',
      },
      {
        name: 'description',
        content: 'Swarm OS - Next Generation Biological Intelligence Interface',
      },
      {
        title: 'Swarm OS Console',
      },
    ],
    links: [
      {
        rel: 'stylesheet',
        href: appCss,
      },
    ],
  }),
  shellComponent: RootDocument,
})

function RootDocument({ children }: { children?: ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
        <HeadContent />
      </head>
      <body className="font-sans antialiased bg-[#04080f] text-[#f0f6fc] m-0 overflow-hidden w-full h-screen relative flex">
        <SwarmTopology3D />
        <Sidebar />
        <div className="flex flex-col flex-1 min-w-0 z-10 relative">
          <Topbar />
          <main className="flex-1 min-w-0 overflow-y-auto p-4 md:p-8 bg-transparent">
            {children}
          </main>
        </div>
        <TanStackDevtools
          config={{
            position: 'bottom-right',
          }}
          plugins={[
            {
              name: 'Tanstack Router',
              render: <TanStackRouterDevtoolsPanel />,
            },
          ]}
        />
        <Scripts />
      </body>
    </html>
  )
}
