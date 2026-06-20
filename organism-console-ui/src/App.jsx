import { useState, useEffect } from 'react';
import { backendHealth } from './lib/api';
import './App.css';

function App() {
  const [health, setHealth] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const checkHealth = async () => {
      const result = await backendHealth();
      setHealth(result);
      setLoading(false);
    };
    checkHealth();
  }, []);

  return (
    <div className="app">
      <h1>Organism Console</h1>
      
      {loading ? (
        <p>Loading...</p>
      ) : health ? (
        <div className="health-status">
          <h2>Backend Status</h2>
          <p>
            {health.healthy ? '✅ Healthy' : '❌ Not Healthy'}
          </p>
          {health.ollama_reachable && (
            <p>🤖 Ollama: Connected</p>
          )}
        </div>
      ) : (
        <p>❌ Backend unreachable</p>
      )}
      
      <div className="features">
        <h2>Features</h2>
        <ul>
          <li>Self-Healing System</li>
          <li>AI Agents</li>
          <li>Adaptive Routing</li>
          <li>Memory & Learning</li>
        </ul>
      </div>
    </div>
  );
}

export default App;
