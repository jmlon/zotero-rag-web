import { QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createBrowserRouter } from "react-router";

import { ChatPage } from "./components/ChatPage";
import { queryClient } from "./hooks/queries";

const router = createBrowserRouter([
  { path: "/", element: <ChatPage /> },
  { path: "/c/:id", element: <ChatPage /> },
  { path: "*", element: <ChatPage /> },
]);

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  );
}
