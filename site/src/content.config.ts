import { defineCollection } from "astro:content";
import { z } from "astro/zod";
import { docsLoader } from "@astrojs/starlight/loaders";
import { docsSchema } from "@astrojs/starlight/schema";

export const collections = {
  docs: defineCollection({
    loader: docsLoader(),
    schema: docsSchema({
      extend: z.object({
        type: z.enum(["tutorial", "guide", "reference", "explanation"]).optional(),
        heroScreenshot: z.string().optional(),
      }),
    }),
  }),
};
