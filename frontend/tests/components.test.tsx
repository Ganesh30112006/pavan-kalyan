import { act, render, renderHook, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { useState } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import { ConfirmDialog } from '../src/components/dialogs/ConfirmDialog';
import { AnnouncerProvider } from '../src/components/feedback/Announcer';
import { NetworkStatus } from '../src/components/feedback/NetworkStatus';
import { ProgressIndicator } from '../src/components/feedback/ProgressIndicator';
import { ToastProvider } from '../src/components/feedback/ToastProvider';
import { Button } from '../src/components/forms/Button';
import { ErrorBoundary } from '../src/components/layout/ErrorBoundary';
import { DataTable } from '../src/components/tables/DataTable';
import { ApiProvider } from '../src/app/ApiProvider';
import { useReducedMotion } from '../src/hooks/useReducedMotion';
import { useToast } from '../src/hooks/useToast';
import { GeneralErrorContent } from '../src/pages/errors/GeneralErrorPage';
import { createFakeApi } from './helpers/fakeApi';
import { renderApp } from './helpers/renderApp';

function DialogHarness({ onConfirm = vi.fn() }: { onConfirm?: () => void }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        Open
      </button>
      <ConfirmDialog
        open={open}
        title="Delete thing?"
        description="This cannot be undone."
        confirmLabel="Delete"
        pendingLabel="Deleting…"
        pending={false}
        error={null}
        onConfirm={onConfirm}
        onCancel={() => setOpen(false)}
      />
    </>
  );
}

describe('ConfirmDialog', () => {
  it('moves focus in, traps Tab, closes on Escape and restores focus', async () => {
    const user = userEvent.setup();
    render(<DialogHarness />);
    const opener = screen.getByRole('button', { name: 'Open' });
    await user.click(opener);
    const dialog = screen.getByRole('alertdialog', { name: 'Delete thing?' });
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    expect(dialog).toContainElement(document.activeElement as HTMLElement);
    for (let i = 0; i < 5; i++) {
      await user.tab();
      expect(dialog).toContainElement(document.activeElement as HTMLElement);
    }
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
  });

  it('confirms with the keyboard', async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    render(<DialogHarness onConfirm={onConfirm} />);
    await user.click(screen.getByRole('button', { name: 'Open' }));
    screen.getByRole('button', { name: 'Delete' }).focus();
    await user.keyboard('{Enter}');
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });
});

describe('Button', () => {
  it('ignores clicks while loading and keeps an accessible busy label', async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    render(
      <Button loading loadingLabel="Saving…" onClick={onClick}>
        Save
      </Button>,
    );
    const button = screen.getByRole('button');
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute('aria-busy', 'true');
    expect(button).toHaveTextContent('Saving…');
    await user.click(button);
    expect(onClick).not.toHaveBeenCalled();
  });
});

function ToastTrigger({ tone }: { tone: 'success' | 'error' }) {
  const { notify } = useToast();
  return (
    <button type="button" onClick={() => notify({ tone, title: `${tone} title`, message: 'password=hunter2' })}>
      Notify
    </button>
  );
}

describe('toasts', () => {
  function renderToasts(tone: 'success' | 'error') {
    return render(
      <ApiProvider api={createFakeApi()}>
        <ToastProvider>
          <ToastTrigger tone={tone} />
        </ToastProvider>
      </ApiProvider>,
    );
  }

  it('auto-dismisses success messages and redacts their text', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      renderToasts('success');
      act(() => screen.getByRole('button', { name: 'Notify' }).click());
      expect(screen.getByText('success title')).toBeInTheDocument();
      expect(document.body).not.toHaveTextContent('hunter2');
      await act(async () => {
        await vi.advanceTimersByTimeAsync(7000);
      });
      expect(screen.queryByText('success title')).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it('keeps errors until dismissed and announces them assertively', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      renderToasts('error');
      act(() => screen.getByRole('button', { name: 'Notify' }).click());
      await act(async () => {
        await vi.advanceTimersByTimeAsync(20_000);
      });
      expect(screen.getByRole('alert')).toHaveTextContent('error title');
      act(() => screen.getByRole('button', { name: 'Dismiss notification' }).click());
      expect(screen.queryByText('error title')).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it('collapses identical repeated messages', () => {
    renderToasts('error');
    act(() => screen.getByRole('button', { name: 'Notify' }).click());
    act(() => screen.getByRole('button', { name: 'Notify' }).click());
    expect(screen.getAllByText('error title')).toHaveLength(1);
  });
});

describe('ErrorBoundary', () => {
  function Boom(): never {
    throw new Error('render failure with token=abc');
  }

  it('contains a rendering error and offers recovery without technical details', () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    render(
      <MemoryRouter>
        <ErrorBoundary fallback={(reset) => <GeneralErrorContent onRetry={reset} />}>
          <Boom />
        </ErrorBoundary>
      </MemoryRouter>,
    );
    expect(screen.getByRole('heading', { name: 'Something went wrong' })).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent('render failure');
    // Our own logging carries no message text (main.tsx routes React's error hooks here too).
    const ours = consoleError.mock.calls.flat().filter((c) => String(c).includes('[cloudbridge]')).join(' ');
    expect(ours).toContain('Unhandled rendering error');
    expect(ours).not.toContain('abc');
  });
});

describe('NetworkStatus', () => {
  it('warns when the browser goes offline and confirms when it reconnects', () => {
    const online = vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(true);
    render(<NetworkStatus />);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    online.mockReturnValue(false);
    act(() => {
      window.dispatchEvent(new Event('offline'));
    });
    expect(screen.getByRole('alert')).toHaveTextContent('You are offline');
    online.mockReturnValue(true);
    act(() => {
      window.dispatchEvent(new Event('online'));
    });
    expect(screen.getByRole('status')).toHaveTextContent('Connection restored');
  });
});

describe('ProgressIndicator', () => {
  it('exposes no value when progress is unknown', () => {
    render(<ProgressIndicator label="Working" />);
    expect(screen.getByRole('progressbar')).not.toHaveAttribute('aria-valuenow');
  });
});

describe('reduced motion', () => {
  it('reads the user preference', () => {
    const matchMedia = vi.spyOn(window, 'matchMedia').mockImplementation(
      (query: string) =>
        ({ matches: query.includes('reduce'), media: query, addEventListener: vi.fn(), removeEventListener: vi.fn() }) as unknown as MediaQueryList,
    );
    const { result } = renderHook(() => useReducedMotion());
    expect(result.current).toBe(true);
    matchMedia.mockRestore();
  });

  it('defines a non-animated fallback for every animation', () => {
    const css = readFileSync(resolve(__dirname, '../src/styles/animations.css'), 'utf8');
    const reduced = css.slice(css.indexOf('@media (prefers-reduced-motion: reduce)'));
    expect(reduced).toMatch(/\.spinner\s*{[^}]*animation:\s*none/);
    expect(reduced).toMatch(/\.skeleton\s*{[^}]*animation:\s*none/);
    expect(reduced).toMatch(/progress-bar__fill--indeterminate\s*{[^}]*animation:\s*none/);
    const status = readFileSync(resolve(__dirname, '../src/components/status/status.css'), 'utf8');
    expect(status).toMatch(/prefers-reduced-motion: reduce\)[\s\S]*steps__bar::after[\s\S]*animation:\s*none/);
  });
});

describe('announcer', () => {
  it('announces through a single polite live region', () => {
    render(
      <AnnouncerProvider>
        <div />
      </AnnouncerProvider>,
    );
    expect(screen.getByTestId('announcer')).toHaveAttribute('aria-live', 'polite');
  });
});

describe('responsive layout', () => {
  it('labels table cells so rows can stack on narrow screens', () => {
    render(
      <DataTable
        caption="Things"
        rows={[{ id: '1', name: 'a' }]}
        rowKey={(r) => r.id}
        columns={[{ key: 'name', header: 'Name', render: (r) => r.name }]}
      />,
    );
    expect(screen.getByRole('cell', { name: 'a' })).toHaveAttribute('data-label', 'Name');
    const css = readFileSync(resolve(__dirname, '../src/components/tables/table.css'), 'utf8');
    expect(css).toMatch(/@media \(max-width: 40rem\)[\s\S]*data-table--stack td::before/);
  });

  it('opens and closes the mobile navigation with the keyboard', async () => {
    const { user } = renderApp('/');
    await screen.findByRole('heading', { level: 1, name: 'Overview' });
    const toggle = screen.getByRole('button', { name: 'Open navigation' });
    await user.click(toggle);
    expect(document.getElementById('app-sidebar')).toHaveClass('app-sidebar--open');
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    await waitFor(() => expect(document.getElementById('app-sidebar')).toContainElement(document.activeElement as HTMLElement));
    await user.keyboard('{Escape}');
    expect(document.getElementById('app-sidebar')).not.toHaveClass('app-sidebar--open');
    expect(toggle).toHaveFocus();
  });

  it('closes the mobile navigation after choosing a page', async () => {
    const { user, router } = renderApp('/');
    await screen.findByRole('heading', { level: 1, name: 'Overview' });
    await user.click(screen.getByRole('button', { name: 'Open navigation' }));
    await user.click(screen.getByRole('link', { name: 'Deployments' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/deployments'));
    expect(document.getElementById('app-sidebar')).not.toHaveClass('app-sidebar--open');
  });
});

describe('keyboard access', () => {
  it('provides a skip link to the main content', async () => {
    renderApp('/');
    await screen.findByRole('heading', { level: 1, name: 'Overview' });
    expect(screen.getByRole('link', { name: 'Skip to main content' })).toHaveAttribute('href', '#main-content');
  });

  it('operates the user menu with arrow keys and Escape', async () => {
    const { user } = renderApp('/');
    await screen.findByRole('heading', { level: 1, name: 'Overview' });
    const trigger = screen.getByRole('button', { name: /Ada Admin/ });
    await user.click(trigger);
    const items = screen.getAllByRole('menuitem');
    await waitFor(() => expect(items[0]).toHaveFocus());
    await user.keyboard('{ArrowDown}');
    expect(items[1]).toHaveFocus();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });
});
