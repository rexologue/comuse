import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createBrowserRouter, RouterProvider } from "react-router";

import { EventsProvider } from "@/api/events";
import { Layout } from "@/app/Layout";
import { ChatPage } from "@/chat/ChatPage";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Home } from "@/pages/Home";
import { SettingsPage } from "@/settings/SettingsPage";
import { Placeholder } from "@/pages/Placeholder";
import { ThemeProvider } from "@/theme";

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 10_000, refetchOnWindowFocus: true } },
});

const router = createBrowserRouter([
  {
    element: <Layout />,
    children: [
      { path: "/", element: <Home /> },
      { path: "/chat/:sessionId", element: <ChatPage /> },
      { path: "/task/:taskId", element: <Placeholder title="Задача" text="Задача без чата: её панель справа." /> },
      { path: "/settings", element: <SettingsPage /> },
      { path: "/settings/:section", element: <SettingsPage /> },
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
