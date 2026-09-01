# Windows

This base project keeps the startup scripts, but release packaging should be owned by each downstream product. 打包产物应内置运行时，不要求用户另行安装开发环境。

Packaged Windows releases should include:

- `runtime\python\python.exe`
- `runtime\node\node.exe`

For source development:

1. Install Python 3.12+ and Node.js 24 LTS.
2. Install backend and frontend dependencies.
3. Run `start.bat` or use the platform-specific development commands.

High-risk destructive local commands are blocked by policy.
