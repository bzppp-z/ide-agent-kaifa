import { create } from "zustand";

import {
  activateProvider as activateProviderRequest,
  createProvider as createProviderRequest,
  deleteProvider as deleteProviderRequest,
  formatError,
  listProviders,
  updateProvider as updateProviderRequest,
  type ProviderInput,
} from "../lib/api";
import type { Provider } from "../lib/types";

interface ProviderState {
  providers: Provider[];
  loading: boolean;
  error: string | null;
  load: () => Promise<void>;
  create: (input: ProviderInput) => Promise<boolean>;
  update: (id: string, input: Partial<ProviderInput>) => Promise<boolean>;
  remove: (id: string) => Promise<boolean>;
  activate: (id: string) => Promise<boolean>;
  reset: () => void;
}

export const useProviderStore = create<ProviderState>((set, get) => ({
  providers: [],
  loading: false,
  error: null,

  load: async () => {
    set({ loading: true, error: null });
    try {
      const providers = await listProviders();
      set({ providers, loading: false });
    } catch (error) {
      set({ loading: false, error: formatError(error) });
    }
  },

  create: async (input) => {
    try {
      await createProviderRequest(input);
      set({ error: null });
      await get().load();
      return true;
    } catch (error) {
      set({ error: formatError(error) });
      return false;
    }
  },

  update: async (id, input) => {
    try {
      await updateProviderRequest(id, input);
      set({ error: null });
      await get().load();
      return true;
    } catch (error) {
      set({ error: formatError(error) });
      return false;
    }
  },

  remove: async (id) => {
    try {
      await deleteProviderRequest(id);
      set({ error: null });
      await get().load();
      return true;
    } catch (error) {
      set({ error: formatError(error) });
      return false;
    }
  },

  activate: async (id) => {
    try {
      const active = await activateProviderRequest(id);
      set((state) => ({
        error: null,
        providers: state.providers.map((provider) =>
          provider.id === active.id
            ? { ...provider, is_active: true }
            : { ...provider, is_active: false },
        ),
      }));
      return true;
    } catch (error) {
      set({ error: formatError(error) });
      return false;
    }
  },

  reset: () => set({ providers: [], loading: false, error: null }),
}));
