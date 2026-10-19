import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "sonner";
import { App } from "@/App";
import { I18nProvider } from "@/i18n";
import { resolveBackend } from "@/lib/api";
import "@/index.css";

/**
 * Server state is TanStack Query's job, with retries off: this is a debugging tool for a
 * local platform, and a hidden retry would turn "the fixture is missing" into a spinner.
 */
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: false,
      refetchOnWindowFocus: false,
    },
  },
});

// Decide once, before the first screen's queries fire, whether the read API is running.
// The probe is fire-and-forget: every API call awaits the same promise, and the banner
// re-renders when it resolves. A failure is not fatal — it selects the fixture backend.
void resolveBackend();

const root = document.getElementById("root");
if (!root) throw new Error("#root is missing from index.html");

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <I18nProvider>
        <BrowserRouter>
          <App />
          <Toaster position="bottom-right" closeButton />
        </BrowserRouter>
      </I18nProvider>
    </QueryClientProvider>
  </StrictMode>,
);
