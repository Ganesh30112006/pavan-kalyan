import { render } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createMemoryRouter } from 'react-router-dom';
import { App } from '../../src/app/App';
import { routes } from '../../src/app/router';
import { createFakeApi, type FakeApi } from './fakeApi';

export function renderApp(path: string, api: FakeApi = createFakeApi()) {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  const user = userEvent.setup();
  const result = render(<App api={api} router={router} />);
  return { ...result, api, router, user };
}
