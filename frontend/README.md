# Question Better. — frontend

Next.js App Router UI for [zivo.fyi](https://zivo.fyi).

## Status

Scaffold ready — branded placeholder home page. Build product screens in `app/`:

- Source upload (PDF, paste, URL)
- Generated question review
- Practice / quiz flow
- Weakness and progress views

## Local development

```bash
npm install
npm run dev
```

Open http://localhost:3000.

Set `NEXT_PUBLIC_API_URL=http://127.0.0.1:8200` in `.env.local` when calling the API from the browser.

## Production

- **Dockerfile** — standalone Next.js build, port 3000
- **Helm** — `infra/k8s/charts/web`
- **Hosts** — `zivo.fyi`, `www.zivo.fyi`

```bash
npm run build
npm run start
```
