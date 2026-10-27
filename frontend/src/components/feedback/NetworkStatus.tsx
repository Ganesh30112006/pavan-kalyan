import { Wifi, WifiOff } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useOnlineStatus } from '../../hooks/useOnlineStatus';
import './feedback.css';

/** Banner shown while the browser is offline, and briefly after it reconnects. */
export function NetworkStatus() {
  const online = useOnlineStatus();
  const [showRestored, setShowRestored] = useState(false);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const onOnline = () => {
      clearTimeout(timer);
      setShowRestored(true);
      timer = setTimeout(() => setShowRestored(false), 4000);
    };
    const onOffline = () => {
      clearTimeout(timer);
      setShowRestored(false);
    };
    window.addEventListener('online', onOnline);
    window.addEventListener('offline', onOffline);
    return () => {
      clearTimeout(timer);
      window.removeEventListener('online', onOnline);
      window.removeEventListener('offline', onOffline);
    };
  }, []);

  if (!online) {
    return (
      <section aria-label="Connection status">
        <div className="network-status" role="alert">
          <WifiOff size={16} aria-hidden="true" />
          You are offline. Data may be out of date and changes cannot be saved until the connection returns.
        </div>
      </section>
    );
  }
  if (showRestored) {
    return (
      <section aria-label="Connection status">
        <div className="network-status network-status--online" role="status">
          <Wifi size={16} aria-hidden="true" />
          Connection restored. Refresh a page to load the latest data.
        </div>
      </section>
    );
  }
  return null;
}
