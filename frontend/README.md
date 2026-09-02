# LZCore Frontend

LZCore 的 React/TypeScript 前端。

## Run

```bash
cd /Users/zhangh01/Desktop/lzcore/frontend
npm run dev -- --host 0.0.0.0
```

The dev server listens on port `5273` and proxies `/api` to `VITE_DEV_API_TARGET`, defaulting to `http://127.0.0.1:8011`.

## Stack

- React 18
- TypeScript
- Vite 8
- Same-origin application router (`src/router.tsx`)
- Zustand
- Axios
- Vitest
- Playwright

## Source Layout

- `src/app/App.tsx`: route table and top-level shell
- `src/router.tsx`: same-origin navigation and search parameters
- `src/api/client.ts`: Axios wrapper and timeout policy
- `src/api/index.ts`: API modules
- `src/pages/`: route pages
- `src/layouts/`: sidebar and app layout
- `src/stores/`: session, workbench, toast state
- `src/types/index.ts`: shared API-facing types
- `src/styles/global.css`: design system and interaction polish
- `src/test/`: Vitest tests
- `e2e/`: Playwright specs

## Commands

```bash
npm run typecheck
npm test -- --run
npm run build
npm run e2e
```

## Notes

- Agent turns use `TIMEOUTS.agentTurn = 180_000`.
- Workbench messages are stored per session.
- Capability state and tool counts come from backend APIs.
- Runtime outcome and recovery-goal state come only from backend AgentResult metadata;
  a failed tool card alone is never sufficient to label the user task failed.
