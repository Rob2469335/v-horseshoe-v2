import { Outlet, useLocation } from 'react-router-dom'
import Sidebar from './Sidebar'
import Topbar from './Topbar'

export default function ShellLayout() {
  const location = useLocation()
  const pageClass = `page-${location.pathname.split('/')[1] || 'ops'}`

  return (
    <div className={`shell ${pageClass}`}>
      <Sidebar />
      <div className="shell__main">
        <Topbar />
        <main className="shell__content">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
