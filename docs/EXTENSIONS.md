# 扩展开发

扩展用于承载领域对象、领域工具、专属 API 和工作台页面。平台发现 bundled `extensions/*/extension.json` 与本地 `plugins/*/extension.json` 中的扩展。

## 创建与校验

```bash
python3 scripts/extension_cli.py create acme.insights --name "洞察工具"
python3 scripts/extension_cli.py validate plugins/acme_insights
```

工具 ID 必须以 `<extension_id>.` 开头；后端路由必须以 `/api/extensions/<extension_id>` 开头；前端路由必须以 `/extensions/<extension_id>` 开头。实现与入口均留在扩展目录中。

## 平台合同

扩展工具不直接执行 handler，而是进入 `ToolRuntimeClient`。平台仍负责 schema、caller、workspace、风险、授权、脱敏、配额、trace 与 audit。扩展的 manifest 声明 API 兼容版本、工具、路由和前端贡献；不兼容清单不得注册。

## 业务对象与网络扩展

业务扩展不仅是工具面板，还要拥有对象模型和完整生命周期。bundled `network.operations` 管理区域、设备、加密的 SSH/Telnet 连接与已发布 Skill。工作台选择只传达候选 Skill；服务端每次调用重新解析 Skill 的设备、连接、工具和 `configuration_write` 范围，选择本身不建立网络连接，也不扩大权限。

模型可以选择 Skill 内的一部分设备。单台连接失败必须以该设备的工具结果返回，不能阻断其他独立设备。读操作是基础能力；网络写操作仅在当前 Skill 明确允许后执行。没有审批弹窗、等待状态或后台批准续跑。

## 恢复集成

扩展不维护自己的 LLM 循环。安全、只读的恢复意图通过 `runtime_recoveries` 列表交给 QueryLoop；每项只描述证据目标与安全观察，不能授予新特权。`network.operations` 的命令语义映射在扩展内，厂商命令模板在驱动内，平台内核不固化厂商 CLI。

## 分发

`.apx` 包对所有文件计算 SHA-256 并使用 Ed25519 签名。私钥仅在发布端保存；安装端只持有公钥。安装拒绝未签名、篡改、超限、重复路径、路径穿越与链接载荷；升级失败时恢复前一版本，卸载移动扩展包而不删除工作区数据。

```bash
python3 scripts/extension_cli.py pack plugins/acme_insights --output dist/acme-insights-0.1.0.apx
python3 scripts/extension_cli.py verify dist/acme-insights-0.1.0.apx
python3 scripts/extension_cli.py publish dist/acme-insights-0.1.0.apx
python3 scripts/extension_cli.py install dist/acme-insights-0.1.0.apx
```
