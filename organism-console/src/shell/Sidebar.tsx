import { NavLink } from 'react-router-dom'
import { appRoutes } from './routes'
import { useUiStore } from '../state/ui-store'

export default function Sidebar() {
  const sidebarCollapsed = useUiStore((state) => state.sidebarCollapsed)

  return (
    <aside className={sidebarCollapsed ? 'sidebar sidebar--collapsed' : 'sidebar'}>
      <div className="sidebar__brand">OC</div>
      <nav className="sidebar__nav">
        {appRoutes.map((route) => (
          <NavLink
            key={route.path}
            to={route.path}
            end={route.path === '/'}
            className={({ isActive }) =>
              isActive ? 'sidebar__link sidebar__link--active' : 'sidebar__link'
            }
          >
            <span className="sidebar__link-text">{route.label}</span>
          </NavLink>
        ))}
      </nav>
    </aside>
  )
}
