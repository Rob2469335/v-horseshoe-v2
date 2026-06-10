from __future__ import annotations

import uvicorn

def main() -> None:
    uvicorn.run(
        "swarm_os.app.main:create_app",
        host="127.0.0.1",
        port=8000,
        reload=False,
        log_level="info",
        factory=True,
    )

if __name__ == "__main__":
    main()
