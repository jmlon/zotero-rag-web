import { QueryClient, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";
import type { Filters } from "../api/types";

export const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, staleTime: 30_000, refetchOnWindowFocus: false } },
});

export const keys = {
  library: ["library"] as const,
  collections: ["collections"] as const,
  conversations: ["conversations"] as const,
  conversation: (id: string) => ["conversation", id] as const,
  tags: (q: string) => ["tags", q] as const,
};

export const useLibrary = () =>
  useQuery({ queryKey: keys.library, queryFn: api.library, refetchInterval: 5 * 60_000 });
export const useCollections = () => useQuery({ queryKey: keys.collections, queryFn: api.collections });
export const useConversations = () =>
  useQuery({ queryKey: keys.conversations, queryFn: api.conversations });
export const useConversation = (id: string | undefined) =>
  useQuery({
    queryKey: keys.conversation(id ?? ""),
    queryFn: () => api.conversation(id!),
    enabled: Boolean(id),
  });
export const useTags = (q: string, enabled: boolean) =>
  useQuery({ queryKey: keys.tags(q), queryFn: () => api.tags(q), enabled });

export function useUpdateConversation(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (change: { title?: string; filters?: Filters }) => api.updateConversation(id, change),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: keys.conversation(id) });
      void client.invalidateQueries({ queryKey: keys.conversations });
    },
  });
}

export function useDeleteConversation() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.deleteConversation(id),
    onSuccess: () => void client.invalidateQueries({ queryKey: keys.conversations }),
  });
}
