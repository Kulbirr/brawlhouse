/* Global app state: branding settings + fighter roster, loaded once at boot. */

import React, { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { api, type Settings, type Fighter } from './api';

interface AppState {
  settings: Settings | null;
  fighters: Fighter[];
  settingsError: string | null;
  fighterName: (regId: string) => string;
  refreshFighters: () => Promise<void>;
}

const Ctx = createContext<AppState | null>(null);

export function AppProvider({ children }: { children: React.ReactNode }) {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [fighters, setFighters] = useState<Fighter[]>([]);
  const [settingsError, setSettingsError] = useState<string | null>(null);

  const refreshFighters = useCallback(async () => {
    try {
      const d = await api<{ fighters: Fighter[] }>('/api/fighters');
      setFighters(d.fighters || []);
    } catch {
      /* keep stale list */
    }
  }, []);

  useEffect(() => {
    (async () => {
      try {
        const s = await api<Settings>('/api/settings');
        setSettings(s);
        document.title = `${s.project_name} | AI Combat Arena`;
      } catch (e) {
        setSettingsError(e instanceof Error ? e.message : 'Could not reach the API');
      }
      await refreshFighters();
    })();
  }, [refreshFighters]);

  const fighterName = useCallback(
    (regId: string) => fighters.find((f) => f.id === regId)?.name || regId,
    [fighters],
  );

  return (
    <Ctx.Provider value={{ settings, fighters, settingsError, fighterName, refreshFighters }}>
      {children}
    </Ctx.Provider>
  );
}

export function useApp(): AppState {
  const v = useContext(Ctx);
  if (!v) throw new Error('useApp outside AppProvider');
  return v;
}
