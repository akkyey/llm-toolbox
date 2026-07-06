# Local LLM Toolbox (StateForge)

A unified toolkit for running, routing, and monitoring local LLMs (like llama.cpp) seamlessly with AI coding agents like Aider.

## Components
1. **Proxy (`proxy/`)**: Intelligent payload routers (`aider_proxy.py`, `kilo_proxy.py`) that handle context compression, prompt rewriting, and error recovery for local models.
2. **Monitor (`monitor/`)**: Watchdog daemon and TUI dashboard to track VRAM usage, token generation speeds, and proxy intervention metrics in real-time.
3. **Scripts (`scripts/`)**: Developer utilities, including `init-aider.sh` for instantly bootstrapping perfect Aider environments with linters and conventions.

## Deployment
See `systemd/` for service files to run the Proxy and Monitor as background daemons.
