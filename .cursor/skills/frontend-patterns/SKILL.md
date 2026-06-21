---
name: frontend-patterns
description: Frontend development patterns for React, Next.js, state management, performance optimization, and UI best practices for this repo's `webapp/`.
---

# Frontend Development Patterns

Modern frontend patterns for React, Next.js, and performant user interfaces.

## Repo Focus

In this repo:

- frontend code lives in `webapp/`
- prefer `apiClient` over ad hoc `fetch` wrappers for app API calls
- use `AuthWrapper` for new auth-gated pages in `webapp/app/`
- if generation UI changes, check `shoot` and `bulk shoot` paths together
- preserve the current product language and interaction patterns instead
  of inventing a new design system per feature

### Auth-gated pages

Use this pattern for every new protected page in `webapp/app/`:

```tsx
function MyPageContent() {
  useEffect(() => {
    void loadData()
  }, [])

  return <div>...</div>
}

export default function MyPage() {
  return (
    <AuthWrapper requireAuth requireVerification>
      <MyPageContent />
    </AuthWrapper>
  )
}
```

Rules:

- Never put authenticated `useEffect` hooks on the outer page component that
  renders `<AuthWrapper>`. Those effects run on mount before auth gating
  completes and can trigger `/login` → `/auth` → `/dashboard` redirects on
  direct URL loads.
- Put API calls in a child component inside `AuthWrapper`, or gate on
  `useAuth()` before calling `apiClient` / `userService`.
- Set `requireVerification={true}` on protected pages so `AuthWrapper`
  waits for the Firebase reload before rendering children.

Use this skill for actual component and state work. Use
`click-path-audit` when a button or UI action appears broken, and use
`design-system` only when the task is truly about visual-system cleanup.
Use `next-best-practices` when the task is specifically about App Router
mechanics, file conventions, Route Handlers, metadata, hydration,
runtime selection, images, fonts, or Server Component boundaries.

## When to Activate

- Building React components (composition, props, rendering)
- Managing state (useState, useReducer, Context, server/client boundaries)
- Implementing data fetching (App Router patterns, server components, `apiClient`)
- Optimizing performance (memoization, virtualization, code splitting)
- Working with forms (validation, controlled inputs, Zod schemas)
- Handling client-side routing and navigation
- Building accessible, responsive UI patterns
- Dealing with framework-level Next.js behavior that overlaps with
  `next-best-practices`

Do not use broad examples from this skill unchanged if they bypass the
repo's existing auth or data-fetching conventions.

## Next.js Agentic Patterns

- Keep LLM calls, provider keys, and heavy agent logic on the server.
  Prefer Server Components, Route Handlers, or Server Actions over
  browser-side orchestration.
- If sensitive utilities must stay server-only, protect them with
  `server-only` so they cannot be imported into Client Components by
  accident.
- Use Route Handlers in `webapp/app/**/route.ts` for custom streaming or
  request/response flows that need direct control over AI responses.
- Use Server Actions for agent-triggered mutations or tool calls, then
  revalidate the affected path or tag so the UI stays in sync.
- Validate Server Action input with Zod before executing tool logic or
  mutations.
- For long-running or agent-like responses, prefer streaming UI and
  loading states so users see progress immediately.
- Use `loading.tsx` or local skeleton states for instant feedback during
  navigation and slow server work.
- Use optimistic UI only when the rollback path is clear; keep optimistic
  state narrow and easy to reverse.
- Keep interactive client logic low in the tree so most of the page can
  stay server-rendered.
- If multiple components need the same server data, rely on the App
  Router's request memoization instead of manually duplicating fetch
  guards.

## Component Patterns

### Composition Over Inheritance

```typescript
// ✅ GOOD: Component composition
interface CardProps {
  children: React.ReactNode
  variant?: 'default' | 'outlined'
}

export function Card({ children, variant = 'default' }: CardProps) {
  return <div className={`card card-${variant}`}>{children}</div>
}

export function CardHeader({ children }: { children: React.ReactNode }) {
  return <div className="card-header">{children}</div>
}

export function CardBody({ children }: { children: React.ReactNode }) {
  return <div className="card-body">{children}</div>
}

// Usage
<Card>
  <CardHeader>Title</CardHeader>
  <CardBody>Content</CardBody>
</Card>
```

### Compound Components

```typescript
interface TabsContextValue {
  activeTab: string
  setActiveTab: (tab: string) => void
}

const TabsContext = createContext<TabsContextValue | undefined>(undefined)

export function Tabs({ children, defaultTab }: {
  children: React.ReactNode
  defaultTab: string
}) {
  const [activeTab, setActiveTab] = useState(defaultTab)

  return (
    <TabsContext.Provider value={{ activeTab, setActiveTab }}>
      {children}
    </TabsContext.Provider>
  )
}

export function TabList({ children }: { children: React.ReactNode }) {
  return <div className="tab-list">{children}</div>
}

export function Tab({ id, children }: { id: string, children: React.ReactNode }) {
  const context = useContext(TabsContext)
  if (!context) throw new Error('Tab must be used within Tabs')

  return (
    <button
      className={context.activeTab === id ? 'active' : ''}
      onClick={() => context.setActiveTab(id)}
    >
      {children}
    </button>
  )
}

// Usage
<Tabs defaultTab="overview">
  <TabList>
    <Tab id="overview">Overview</Tab>
    <Tab id="details">Details</Tab>
  </TabList>
</Tabs>
```

### Render Props Pattern

```typescript
interface DataLoaderProps<T> {
  url: string
  children: (data: T | null, loading: boolean, error: Error | null) => React.ReactNode
}

export function DataLoader<T>({ url, children }: DataLoaderProps<T>) {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<Error | null>(null)

  useEffect(() => {
    apiClient.get(url)
      .then(res => res.json())
      .then(setData)
      .catch(setError)
      .finally(() => setLoading(false))
  }, [url])

  return <>{children(data, loading, error)}</>
}

// Usage
<DataLoader<Item[]> url="/api/v1/items">
  {(items, loading, error) => {
    if (loading) return <Spinner />
    if (error) return <Error error={error} />
    return <ItemList items={items!} />
  }}
</DataLoader>
```

## Custom Hooks Patterns

### State Management Hook

```typescript
export function useToggle(initialValue = false): [boolean, () => void] {
  const [value, setValue] = useState(initialValue)

  const toggle = useCallback(() => {
    setValue(v => !v)
  }, [])

  return [value, toggle]
}

// Usage
const [isOpen, toggleOpen] = useToggle()
```

### Async Data Fetching Hook

```typescript
interface UseQueryOptions<T> {
  onSuccess?: (data: T) => void
  onError?: (error: Error) => void
  enabled?: boolean
}

export function useQuery<T>(
  key: string,
  fetcher: () => Promise<T>,
  options?: UseQueryOptions<T>
) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const [loading, setLoading] = useState(false)

  const refetch = useCallback(async () => {
    setLoading(true)
    setError(null)

    try {
      const result = await fetcher()
      setData(result)
      options?.onSuccess?.(result)
    } catch (err) {
      const error = err as Error
      setError(error)
      options?.onError?.(error)
    } finally {
      setLoading(false)
    }
  }, [fetcher, options])

  useEffect(() => {
    if (options?.enabled !== false) {
      refetch()
    }
  }, [key, refetch, options?.enabled])

  return { data, error, loading, refetch }
}

// Usage
const { data: items, loading, error, refetch } = useQuery(
  'items',
  () => apiClient.get('/api/v1/items').then(r => r.data),
  {
    onSuccess: data => console.log('Fetched', data.length, 'items'),
    onError: err => console.error('Failed:', err)
  }
)
```

### Debounce Hook

```typescript
export function useDebounce<T>(value: T, delay: number): T {
  const [debouncedValue, setDebouncedValue] = useState<T>(value)

  useEffect(() => {
    const handler = setTimeout(() => {
      setDebouncedValue(value)
    }, delay)

    return () => clearTimeout(handler)
  }, [value, delay])

  return debouncedValue
}

// Usage
const [searchQuery, setSearchQuery] = useState('')
const debouncedQuery = useDebounce(searchQuery, 500)

useEffect(() => {
  if (debouncedQuery) {
    performSearch(debouncedQuery)
  }
}, [debouncedQuery])
```

## State Management Patterns

### Context + Reducer Pattern

```typescript
interface State {
  shoots: ShootSummary[]
  selectedShoot: ShootSummary | null
  loading: boolean
}

type Action =
  | { type: 'SET_SHOOTS'; payload: ShootSummary[] }
  | { type: 'SELECT_SHOOT'; payload: ShootSummary }
  | { type: 'SET_LOADING'; payload: boolean }

function reducer(state: State, action: Action): State {
  switch (action.type) {
    case 'SET_SHOOTS':
      return { ...state, shoots: action.payload }
    case 'SELECT_SHOOT':
      return { ...state, selectedShoot: action.payload }
    case 'SET_LOADING':
      return { ...state, loading: action.payload }
    default:
      return state
  }
}

const ShootContext = createContext<{
  state: State
  dispatch: Dispatch<Action>
} | undefined>(undefined)

export function ShootProvider({ children }: { children: React.ReactNode }) {
  const [state, dispatch] = useReducer(reducer, {
    shoots: [],
    selectedShoot: null,
    loading: false
  })

  return (
    <ShootContext.Provider value={{ state, dispatch }}>
      {children}
    </ShootContext.Provider>
  )
}

export function useShoots() {
  const context = useContext(ShootContext)
  if (!context) throw new Error('useShoots must be used within ShootProvider')
  return context
}
```

## Performance Optimization

### Memoization

```typescript
// ✅ useMemo for expensive computations
const sortedShoots = useMemo(() => {
  return [...shoots].sort((a, b) => b.createdAt.localeCompare(a.createdAt))
}, [shoots])

// ✅ useCallback for functions passed to children
const handleSearch = useCallback((query: string) => {
  setSearchQuery(query)
}, [])

// ✅ React.memo for pure components
export const ShootCard = React.memo<ShootCardProps>(({ shoot }) => {
  return (
    <div className="shoot-card">
      <h3>{shoot.name}</h3>
      <p>{shoot.status}</p>
    </div>
  )
})
```

### Code Splitting & Lazy Loading

```typescript
import { lazy, Suspense } from 'react'

// ✅ Lazy load heavy components
const HeavyChart = lazy(() => import('./HeavyChart'))
const ThreeJsBackground = lazy(() => import('./ThreeJsBackground'))

export function Dashboard() {
  return (
    <div>
      <Suspense fallback={<ChartSkeleton />}>
        <HeavyChart data={data} />
      </Suspense>

      <Suspense fallback={null}>
        <ThreeJsBackground />
      </Suspense>
    </div>
  )
}
```

### Virtualization for Long Lists

```typescript
import { useVirtualizer } from '@tanstack/react-virtual'

export function VirtualShootList({ shoots }: { shoots: ShootSummary[] }) {
  const parentRef = useRef<HTMLDivElement>(null)

  const virtualizer = useVirtualizer({
    count: shoots.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 100,  // Estimated row height
    overscan: 5  // Extra items to render
  })

  return (
    <div ref={parentRef} style={{ height: '600px', overflow: 'auto' }}>
      <div
        style={{
          height: `${virtualizer.getTotalSize()}px`,
          position: 'relative'
        }}
      >
        {virtualizer.getVirtualItems().map(virtualRow => (
          <div
            key={virtualRow.index}
            style={{
              position: 'absolute',
              top: 0,
              left: 0,
              width: '100%',
              height: `${virtualRow.size}px`,
              transform: `translateY(${virtualRow.start}px)`
            }}
          >
            <ShootCard shoot={shoots[virtualRow.index]} />
          </div>
        ))}
      </div>
    </div>
  )
}
```

## Form Handling Patterns

### Controlled Form with Validation

```typescript
interface FormData {
  name: string
  description: string
  endDate: string
}

interface FormErrors {
  name?: string
  description?: string
  endDate?: string
}

export function CreateShootForm() {
  const [formData, setFormData] = useState<FormData>({
    name: '',
    description: '',
    endDate: ''
  })

  const [errors, setErrors] = useState<FormErrors>({})

  const validate = (): boolean => {
    const newErrors: FormErrors = {}

    if (!formData.name.trim()) {
      newErrors.name = 'Name is required'
    } else if (formData.name.length > 200) {
      newErrors.name = 'Name must be under 200 characters'
    }

    if (!formData.description.trim()) {
      newErrors.description = 'Description is required'
    }

    if (!formData.endDate) {
      newErrors.endDate = 'End date is required'
    }

    setErrors(newErrors)
    return Object.keys(newErrors).length === 0
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()

    if (!validate()) return

    try {
      await createShoot(formData)
      // Success handling
    } catch (error) {
      // Error handling
    }
  }

  return (
    <form onSubmit={handleSubmit}>
      <input
        value={formData.name}
        onChange={e => setFormData(prev => ({ ...prev, name: e.target.value }))}
        placeholder="Shoot name"
      />
      {errors.name && <span className="error">{errors.name}</span>}

      {/* Other fields */}

      <button type="submit">Create Shoot</button>
    </form>
  )
}
```

## Error Boundary Pattern

```typescript
interface ErrorBoundaryState {
  hasError: boolean
  error: Error | null
}

export class ErrorBoundary extends React.Component<
  { children: React.ReactNode },
  ErrorBoundaryState
> {
  state: ErrorBoundaryState = {
    hasError: false,
    error: null
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    console.error('Error boundary caught:', error, errorInfo)
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="error-fallback">
          <h2>Something went wrong</h2>
          <p>{this.state.error?.message}</p>
          <button onClick={() => this.setState({ hasError: false })}>
            Try again
          </button>
        </div>
      )
    }

    return this.props.children
  }
}

// Usage
<ErrorBoundary>
  <App />
</ErrorBoundary>
```

## Animation Patterns

### Framer Motion Animations

```typescript
import { motion, AnimatePresence } from 'framer-motion'

// ✅ List animations
export function AnimatedShootList({ shoots }: { shoots: ShootSummary[] }) {
  return (
    <AnimatePresence>
      {shoots.map(shoot => (
        <motion.div
          key={shoot.id}
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -20 }}
          transition={{ duration: 0.3 }}
        >
          <ShootCard shoot={shoot} />
        </motion.div>
      ))}
    </AnimatePresence>
  )
}

// ✅ Modal animations
export function Modal({ isOpen, onClose, children }: ModalProps) {
  return (
    <AnimatePresence>
      {isOpen && (
        <>
          <motion.div
            className="modal-overlay"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={onClose}
          />
          <motion.div
            className="modal-content"
            initial={{ opacity: 0, scale: 0.9, y: 20 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.9, y: 20 }}
          >
            {children}
          </motion.div>
        </>
      )}
    </AnimatePresence>
  )
}
```

## Accessibility Patterns

### Keyboard Navigation

```typescript
export function Dropdown({ options, onSelect }: DropdownProps) {
  const [isOpen, setIsOpen] = useState(false)
  const [activeIndex, setActiveIndex] = useState(0)

  const handleKeyDown = (e: React.KeyboardEvent) => {
    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault()
        setActiveIndex(i => Math.min(i + 1, options.length - 1))
        break
      case 'ArrowUp':
        e.preventDefault()
        setActiveIndex(i => Math.max(i - 1, 0))
        break
      case 'Enter':
        e.preventDefault()
        onSelect(options[activeIndex])
        setIsOpen(false)
        break
      case 'Escape':
        setIsOpen(false)
        break
    }
  }

  return (
    <div
      role="combobox"
      aria-expanded={isOpen}
      aria-haspopup="listbox"
      onKeyDown={handleKeyDown}
    >
      {/* Dropdown implementation */}
    </div>
  )
}
```

### Focus Management

```typescript
export function Modal({ isOpen, onClose, children }: ModalProps) {
  const modalRef = useRef<HTMLDivElement>(null)
  const previousFocusRef = useRef<HTMLElement | null>(null)

  useEffect(() => {
    if (isOpen) {
      // Save currently focused element
      previousFocusRef.current = document.activeElement as HTMLElement

      // Focus modal
      modalRef.current?.focus()
    } else {
      // Restore focus when closing
      previousFocusRef.current?.focus()
    }
  }, [isOpen])

  return isOpen ? (
    <div
      ref={modalRef}
      role="dialog"
      aria-modal="true"
      tabIndex={-1}
      onKeyDown={e => e.key === 'Escape' && onClose()}
    >
      {children}
    </div>
  ) : null
}
```

**Remember**: Modern frontend patterns enable maintainable, performant user interfaces. Choose patterns that fit your project complexity.
