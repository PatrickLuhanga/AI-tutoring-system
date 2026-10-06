import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { ChatProvider } from './state/chat.tsx'
import { SessionProvider } from './state/session.tsx'
import { ThemeProvider } from './state/theme.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ThemeProvider>
      <SessionProvider>
        <ChatProvider>
          <App />
        </ChatProvider>
      </SessionProvider>
    </ThemeProvider>
  </StrictMode>,
)