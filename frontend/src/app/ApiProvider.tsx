import type { ReactNode } from 'react';
import type { CloudBridgeApi } from '../services/api';
import { ApiContext } from './contexts';

export function ApiProvider({ api, children }: { api: CloudBridgeApi; children: ReactNode }) {
  return <ApiContext.Provider value={api}>{children}</ApiContext.Provider>;
}
