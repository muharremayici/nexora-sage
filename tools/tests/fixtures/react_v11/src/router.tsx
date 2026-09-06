import { createRootRoute, createRoute, createRouter } from '@tanstack/react-router';
import { Links, Meta, Scripts, ScrollRestoration } from 'react-router';

const rootRoute = createRootRoute({ component: () => <main>Root</main> });
const settingsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings',
  component: () => <main>Settings</main>,
});

export const router = createRouter({ routeTree: rootRoute.addChildren([settingsRoute]) });

export function FrameworkShell() {
  return <><Meta /><Links /><ScrollRestoration /><Scripts /></>;
}
