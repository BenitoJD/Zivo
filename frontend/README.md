# Question Better. — frontend

Next.js 16 App Router UI for [zivo.fyi](https://zivo.fyi).

## UI policy

**Mantine only. No custom components.**

All screens are built from `@mantine/*` primitives directly in `app/` routes. There is no `components/` directory and no custom CSS.

| Allowed | Forbidden |
|---------|-----------|
| `@mantine/core`, `@mantine/hooks`, `@mantine/form`, `@mantine/dropzone`, `@mantine/notifications` | `frontend/components/` |
| `@tabler/icons-react` | Tailwind, shadcn, custom `.css` files |
| `app/providers.tsx` (`MantineProvider` only) | Hand-rolled buttons, modals, layouts |
| `lib/` for API client, types, constants | UI abstractions in `lib/` |

## Structure

```
frontend/app/
├── layout.tsx                    # root layout + Mantine styles
├── providers.tsx                 # MantineProvider + Notifications
├── page.tsx                      # redirect → /workspace
└── workspace/
    ├── layout.tsx                # AppShell, sidebar, add-source + auth modals
    ├── page.tsx                  # empty library
    └── [artifactId]/page.tsx     # page setup, MCQ, source, tutor chat
```

## Local development

```bash
npm install
npm run dev
```

Open http://localhost:3000.

Leave `NEXT_PUBLIC_*` empty so the browser uses same-origin Next rewrites to
auth, storage, and the other HTTP services.

```bash
npm run build
npm run lint
```

## Production

- **Dockerfile** — standalone Next.js build, port 3000
- **Helm** — `infra/k8s/charts/web`
- **Hosts** — `zivo.fyi`, `www.zivo.fyi`

Agent conventions: [AGENTS.md](../AGENTS.md#frontend-ui).
