# 联智中枢前端

前端使用 React 18、TypeScript、Vite 8、Zustand、Axios、Vitest 与 Playwright，源码位于 `frontend/src/`。

## 开发与验证

```bash
cd frontend
npm run dev -- --host 127.0.0.1
npm run typecheck
npm test -- --run
npm run build
npm run e2e
```

开发服务器默认端口为 `5273`，`/api` 代理到 `VITE_DEV_API_TARGET`（默认 `http://127.0.0.1:8011`）。

## 代码导航

- `src/app/App.tsx`：顶层应用与导航路由。
- `src/api/`：API 客户端与资源模块。
- `src/pages/`、`src/layouts/`、`src/components/`：页面、布局与组件。
- `src/stores/`：Zustand 视图状态。
- `src/types/`：API 面向的类型。
- `src/test/`、`e2e/`：单元和浏览器测试。

前端只呈现服务端事实：不得在浏览器补充 `workspace_id`、Skill 权限、任务终态或恢复目标。登录使用 HttpOnly 会话；临时 token 仅可存于 sessionStorage，不能写入 URL、localStorage、日志或构建变量。
