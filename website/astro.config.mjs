// @ts-check
import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';
import starlightBlog from 'starlight-blog';

/**
 * The site is served as a GitHub Pages *project* page, so it lives under
 * `/<repo>/`. `site` + `base` are what make every generated absolute URL
 * (canonical, RSS, sitemap, asset paths) correct.
 */
export default defineConfig({
	site: 'https://zjunlp.github.io',
	base: '/HarnessGrad',
	integrations: [
		starlight({
			title: 'HarnessGrad',
			description:
				'The gradient for harness self-improvement: measurable, attributable, comparable.',
			favicon: '/favicon.svg',
			logo: { src: './src/assets/mark.svg', alt: '' },
			customCss: ['./src/styles/custom.css'],
			head: [
				{
					// Collapse the left sidebar, remember the choice, and put the control where the
					// reader can reach it on any page. Written as a head script rather than a
					// component override so Starlight's own sidebar markup stays untouched.
					tag: 'script',
					content: `(() => {
  const KEY = 'hg-sidebar-off';
  const root = document.documentElement;
  const apply = (off) => {
    root.classList.toggle('hg-sidebar-off', off);
  };
  try { if (localStorage.getItem(KEY) === '1') apply(true); } catch {}
  const mount = () => {
    const toggle = document.createElement('button');
    toggle.className = 'hg-sidebar-toggle';
    toggle.type = 'button';
    toggle.setAttribute('aria-label', 'Collapse the sidebar');
    toggle.title = 'Collapse or expand the sidebar';
    const paint = () => {
      const off = root.classList.contains('hg-sidebar-off');
      toggle.textContent = off ? '\\u203a' : '\\u2039';
      toggle.setAttribute('aria-expanded', String(!off));
    };
    toggle.addEventListener('click', () => {
      const off = !root.classList.contains('hg-sidebar-off');
      apply(off);
      try { localStorage.setItem(KEY, off ? '1' : '0'); } catch {}
      paint();
    });
    paint();
    document.body.appendChild(toggle);
  };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mount);
  } else {
    mount();
  }
})();`,
				},
			],

			social: [
				{
					icon: 'github',
					label: 'GitHub',
					href: 'https://github.com/zjunlp/HarnessGrad',
				},
			],
			editLink: {
				baseUrl: 'https://github.com/zjunlp/HarnessGrad/edit/main/website/',
			},
			// Two independent sections, as the plugin's multiple-instances guide
			// describes: posts for each live in `src/content/docs/<prefix>/`.
			plugins: [
				starlightBlog([
					{ prefix: 'news', title: 'News', navigation: 'header-start' },
					{ prefix: 'blog', title: 'Blog', navigation: 'header-end' },
				]),
			],
			sidebar: [
				{
					label: 'Start here',
					items: [
						{ label: 'Getting started', slug: 'getting-started' },
						{ label: 'Status and limits', slug: 'status' },
					],
				},
				{
					label: 'Concepts',
					items: [{ autogenerate: { directory: 'concepts' } }],
				},
				{
					label: 'Benchmarks',
					items: [{ autogenerate: { directory: 'benchmarks' } }],
				},
				{
					label: 'Methods',
					items: [{ autogenerate: { directory: 'methods' } }],
				},
				{
					label: 'Contribute',
					items: [{ label: 'Contributing results', slug: 'contributing' }],
				},
			],
		}),
	],
});
