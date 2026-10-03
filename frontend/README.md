# Agent Desk: the site

The Next.js site for Agent Desk. See the [README one folder up](../README.md) for what the
project is and how to run it.

```powershell
npm install
npm run dev      # http://localhost:3010, expects the API on http://127.0.0.1:8010
npm run lint
npx tsc --noEmit
npm run build
```

Set `NEXT_PUBLIC_API_URL` if the API runs somewhere else.
