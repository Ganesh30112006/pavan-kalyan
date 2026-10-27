import type { Blocker } from 'react-router-dom';
import { Button } from '../forms/Button';
import { Dialog } from './Dialog';

/** Shown when the user tries to leave a form with unsaved changes. */
export function UnsavedChangesDialog({ blocker }: { blocker: Blocker }) {
  if (blocker.state !== 'blocked') return null;
  return (
    <Dialog
      open
      title="Discard unsaved changes?"
      onClose={() => blocker.reset()}
      role="alertdialog"
      footer={
        <>
          <Button variant="primary" onClick={() => blocker.reset()}>
            Keep editing
          </Button>
          <Button variant="danger" onClick={() => blocker.proceed()}>
            Discard changes
          </Button>
        </>
      }
    >
      <p>You have changes that have not been saved. If you leave this page, they will be lost.</p>
    </Dialog>
  );
}
