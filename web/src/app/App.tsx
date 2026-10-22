import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createBrowserRouter, RouterProvider } from "react-router";

import { Layout } from "@/app/Layout";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Home } from "@/pages/Home";
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
      { path: "/chat/:sessionId", element: <Placeholder title="Чат" text="Здесь будет чат с агентом — следующий шаг." /> },
      { path: "/task/:taskId", element: <Placeholder title="Задача" text="Задача без чата: её панель справа." /> },
      { path: "/settings", element: <Placeholder title="Настройки" text="Модели, рабочие настройки, аккаунты и «О программе» — шаг 6." /> },
    ],
  },
]);

export function App() {
  return (
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <TooltipProvider>
          <RouterProvider router={router} />
        </TooltipProvider>
      </QueryClientProvider>
    </ThemeProvider>
  );
}
