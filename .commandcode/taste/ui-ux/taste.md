# UI/UX Preferences

- "Think Steve Jobs" / "Think Apple" — premium, fluid, world-class feel. No half-measures on polish. This is the single most repeated UX directive. Confidence: 1.0
- Dark mode and light mode both must work. Default is light mode; dark mode only when user selects it. Buttons, text, icons must be visible in both. Confidence: 0.95
- Generation screen must never show "0%" — progress must be realistic, dynamic, and stage-weighted. The user should always understand what's happening. Confidence: 0.9
- Seamless: user should never wait more than ~5 seconds. Everything long-running must happen in the background. Confidence: 0.98
- Chat streaming must be word-by-word, letter-by-letter — not one-shot response. Like ChatGPT. Confidence: 0.9
- Per-mode chat: fully separate conversations per study mode, not one global chat. Each mode should have independent context. Confidence: 0.85
- Chat must know the current question, the user's selected answer, and whether they've checked it — full awareness of the study state. Confidence: 0.85
- Responsive across all devices: mobile, tablet, laptop, desktop, TV. Pinch-to-zoom on mobile PDF. Confidence: 0.95
- Scrollbar should be subtle, thin, and in sync with the app's design — not a distracting default browser scrollbar. Confidence: 0.85
- Mode switching should be intuitive and discoverable — modes grouped logically (core vs. tools), SegmentedControl pattern. Confidence: 0.8
- Empty states, loading states, and transitions need polished animations — never a flash of unstyled content or a jarring jump. Confidence: 0.9
- Any interactive element should feel fluid: sidebar expand, panel drag/resize, between-question transitions. Confidence: 0.9
- Prefers Chrome-style PDF page thumbnail sidebar for navigation. Confidence: 0.85
- Pet/character animations should be colorful, walking around, not static. Should feel premium. Off by default (toggle in Settings). Confidence: 0.8
- The generation flow should be legible even to a 10-year-old — simple language explaining what's happening. Confidence: 0.9
- Full-screen, floating, draggable/resizable chat window pattern (modern, not old-school expand-from-side). Confidence: 0.8
