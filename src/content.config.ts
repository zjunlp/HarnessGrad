import { defineCollection } from 'astro:content';
import { docsLoader, i18nLoader } from '@astrojs/starlight/loaders';
import { docsSchema, i18nSchema } from '@astrojs/starlight/schema';
import { blogSchema } from 'starlight-blog/schema';

export const collections = {
	i18n: defineCollection({ loader: i18nLoader(), schema: i18nSchema() }),
	docs: defineCollection({
		loader: docsLoader(),
		// `blogSchema` is what adds the frontmatter (`date`, `authors`, `tags`,
		// cover image, ...) that the blog and news instances read.
		schema: docsSchema({ extend: (context) => blogSchema(context) }),
	}),
};
