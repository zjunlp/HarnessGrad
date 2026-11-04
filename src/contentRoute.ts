import { defineRouteMiddleware } from '@astrojs/starlight/route-data';

// Journal pages use a quiet reading layout, not the documentation tree. The
// plugin still owns article metadata, RSS, tags, pagination and structured data.
export const onRequest = defineRouteMiddleware((context) => {
  if (/^(news|blog)(\/|$)/.test(context.locals.starlightRoute.id)) {
    context.locals.starlightRoute.hasSidebar = false;
  }
});
