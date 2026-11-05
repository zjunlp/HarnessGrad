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
			title: 'HarnessGrad: Harness as A Continual Learner',
			defaultLocale: 'root',
			locales: { root: { label: 'English', lang: 'en' }, zh: { label: '简体中文', lang: 'zh-CN' } },
			components: {
				Header: './src/components/ContentHeader.astro',
				PageTitle: './src/components/ContentTitle.astro',
			},
			routeMiddleware: './src/contentRoute.ts',
			// Describe current capabilities plainly; continual learning is a research ambition.
			description:
				'Optimize the code around a fixed model using environment feedback. HarnessGrad runs and measures harness-improvement experiments.',
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
    if (!root.hasAttribute('data-has-sidebar')) { apply(false); return; }
    const toggle = document.createElement('button');
    toggle.className = 'hg-sidebar-toggle';
    toggle.type = 'button';
    toggle.setAttribute('aria-label', 'Collapse the sidebar');
    toggle.title = 'Collapse or expand the sidebar';
    const paint = () => {
      const off = root.classList.contains('hg-sidebar-off');
      toggle.textContent = off ? '\\u203a' : '\\u2039';
      toggle.setAttribute('aria-expanded', String(!off));
      toggle.setAttribute('aria-label', off ? 'Expand the sidebar' : 'Collapse the sidebar');
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
				baseUrl: 'https://github.com/zjunlp/HarnessGrad/edit/website/',
			},
			// Two independent sections, as the plugin's multiple-instances guide
			// describes: posts for each live in `src/content/docs/<prefix>/`.
			plugins: [
				starlightBlog([
					{ prefix: 'news', title: 'News', navigation: 'none' },
					{ prefix: 'blog', title: 'Blog', navigation: 'none' },
				]),
			],
			sidebar: [
				{
					label: 'Start here', translations: { zh: '开始使用' },
					items: [{ label: 'Getting started', translations: { zh: '快速入门' }, slug: 'getting-started' }],
				},
				{
					label: 'Benchmarks', translations: { zh: '基准测试' },
					items: [{ autogenerate: { directory: 'benchmarks' } }],
				},
				{
					label: 'Methods', translations: { zh: '方法' },
					items: [{ autogenerate: { directory: 'methods' } }],
				},
				{
					label: 'Contribute', translations: { zh: '参与贡献' },
					items: [
						{ label: 'Submit a harness', translations: { zh: '提交 harness' }, slug: 'contributing' },
						{ label: '5-minute upload', translations: { zh: '五分钟上传' }, slug: 'submit-harness' },
					],
				},
			],
		}),
	],
});
