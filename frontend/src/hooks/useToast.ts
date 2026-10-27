import { useContext } from 'react';
import { ToastContext, type ToastContextValue } from '../app/contexts';

export function useToast(): ToastContextValue {
  const toast = useContext(ToastContext);
  if (!toast) throw new Error('useToast must be used inside <ToastProvider>');
  return toast;
}
