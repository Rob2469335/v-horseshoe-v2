import StatusBadge from '../components/StatusBadge'
import { useUiStore } from '../state/ui-store'

export default function Topbar() {
  const selectedWorkspace = useUiStore((state) => state.selectedWorkspace)
  const backendUrl = useUiStore((state) => state.backendUrl)
  const connectionStatus = useUiStore((state) => state.connectionStatus)
  const toggleSidebar = useUiStore((state) => state.toggleSidebar)
  const setBackendUrl = useUiStore((state) => state.setBackendUrl)
  const setConnectionStatus = useUiStore((state) => state.setConnectionStatus)

  const tone =
    connectionStatus === 'online'
      ? 'success'
      : connectionStatus === 'offline'
      ? 'danger'
      : connectionStatus === 'connecting'
      ? 'warning'
      : 'neutral'

  return (
    <header className="topbar">
      <div className="topbar__left">
        <button className="topbar__button" type="button" onClick={toggleSidebar}>
          Toggle
        </button>
        <div className="topbar__meta">
          <span className="topbar__label">Workspace</span>
          <strong>{selectedWorkspace}</strong>
        </div>
      </div>

      <div className="topbar__right">
        <label className="topbar__field">
          <span className="topbar__label">Backend</span>
          <input
            className="topbar__input"
            type="text"
            value={backendUrl}
            onChange={(event) => {
              setBackendUrl(event.target.value)
              setConnectionStatus('unknown')
            }}
          />
        </label>
        <StatusBadge label={connectionStatus} tone={tone} />
        <button className="topbar__button" type="button">Theme</button>
        <button className="topbar__button" type="button" onClick={() => setConnectionStatus('connecting')}>
          Reconnect
        </button>
      </div>
    </header>
  )
}
