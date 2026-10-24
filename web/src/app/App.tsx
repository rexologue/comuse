import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { lazy, Suspense } from "react";
import { createBrowserRouter, RouterProvider } from "react-router";

import { EventsProvider } from "@/api/events";
import { Layout } from "@/app/Layout";
import { ChatRoute } from "@/chat/ChatPage";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Home } from "@/pages/Home";
import { Placeholder } from "@/pages/Placeholder";
import { ThemeProvider } from "@/theme";

// Settings are opened now and then: their code loads when asked for.
const SettingsPage = lazy(() => import("@/settings/SettingsPage").then((module) => ({ default: module.SettingsPage })));

const settings = (
  <Suspense fallback={<p className="p-6 text-sm text-muted-foreground">Загрузка…</p>}>
    <SettingsPage />
  </Suspense>
);

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 10_000, refetchOnWindowFocus: true } },
});

const router = createBrowserRouter([
  {
    element: <Layout />,
    children: [
      { path: "/", element: <Home /> },
      { path: "/chat/:sessionId", element: <ChatRoute /> },
      { path: "/task/:taskId", element: <Placeholder title="Задача" text="Задача без чата: её панель справа." /> },
      { path: "/settings", element: settings },
      { path: "/settings/:section", element: settings },
    ],
  },
]);

export function App() {
  return (
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <EventsProvider>
          <TooltipProvider>
            <RouterProvider router={router} />
          </TooltipProvider>
        </EventsProvider>
      </QueryClientProvider>
    </ThemeProvider>
  );
}
