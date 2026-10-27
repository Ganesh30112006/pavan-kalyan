import { useContext } from 'react';
import { ApiContext } from '../app/contexts';
import type { CloudBridgeApi } from '../services/api';

export function useApi(): CloudBridgeApi {
  const api = useContext(ApiContext);
  if (!api) throw new Error('useApi must be used inside <ApiProvider>');
  return api;
}

export function useDemoMode(): boolean {
  return useApi().mode === 'demo';
}
