# Windows 本地运行

Windows 启动与停止入口是仓库根目录的 `start.bat`、`stop.bat`、`start.ps1` 与 `stop.ps1`。它们遵循与 macOS/Linux `start.sh`、`stop.sh` 相同的端口、认证和进程归属原则。

Windows 发行运行不要求用户另行安装开发环境；发行包使用 `runtime\python\python.exe` 和 `runtime\node\node.exe` 提供所需运行时。源码开发仍应使用仓库声明的 Python、Node.js 与依赖版本，不能把开发机环境假定为最终用户环境。

使用 PowerShell 时在仓库根目录执行：

```powershell
.\start.ps1
.\stop.ps1
```

默认前端端口为 `5273`，后端健康接口为 `http://127.0.0.1:8011/api/health`。网络监听前必须配置有效认证；不要为了排障禁用启动安全策略。修改 Windows 脚本后运行 `harness/test_windows_runtime_contract.py`。
