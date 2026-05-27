import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import "./index.css";
import { TicketPlanPage } from "./pages/TicketPlanPage";
import { HomePage } from "./pages/HomePage";
import { IssuesPage } from "./pages/IssuesPage";
import { NewTicketPage } from "./pages/NewTicketPage";
import { RunsPage } from "./pages/RunsPage";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, refetchOnWindowFocus: false },
  },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/new" element={<NewTicketPage />} />
          <Route path="/issues" element={<IssuesPage />} />
          <Route path="/runs" element={<RunsPage />} />
          <Route path="/tickets/:externalId" element={<TicketPlanPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);
