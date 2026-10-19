import { defineConfig } from "astro/config";
import starlight from "@astrojs/starlight";

// Both code themes use navy surfaces and a measured, high-contrast token palette.
const codeTheme = (name, type) => ({
  name,
  type,
  colors: {
    "editor.background": "#0B1F3B",
    "editor.foreground": "#E6F4FF",
    "terminal.background": "#0B1F3B",
    "terminal.foreground": "#E6F4FF",
    "scrollbarSlider.background": "#496783",
    "scrollbarSlider.hoverBackground": "#B5CBE0",
  },
  tokenColors: [
    { scope: ["comment"], settings: { foreground: "#B5CBE0" } },
    { scope: ["string"], settings: { foreground: "#B8DFC6" } },
    { scope: ["keyword", "storage"], settings: { foreground: "#91C4FF" } },
    { scope: ["constant", "entity", "support", "variable"], settings: { foreground: "#E6F4FF" } },
  ],
});

export default defineConfig({
  site: "https://filterhelm.nulthara.com",
  integrations: [
    starlight({
      title: "FilterHelm",
      description: "FilterHelm is a self-hosted web app for managing several Pi-hole instances from one place.",
      logo: {
        src: "./src/assets/filterhelm-icon-compact.svg",
        replacesTitle: false,
      },
      favicon: "/favicon.svg",
      social: [{ icon: "github", label: "GitHub", href: "https://github.com/nulthara/filterhelm" }],
      customCss: ["@fontsource-variable/inter", "./src/styles/theme.css"],
      components: {
        Hero: "./src/components/Hero.astro",
        PageTitle: "./src/components/PageTitle.astro",
        Footer: "./src/components/Footer.astro",
      },
      sidebar: [
        { label: "Start here", items: [{ slug: "start/first-run" }] },
        { label: "Guides", items: [{ slug: "guides/install-options" }, { slug: "guides/manage-instances" }] },
        { label: "Reference", items: [{ slug: "reference/pages" }] },
        { label: "Understand", items: [{ slug: "understand/safety-model" }] },
      ],
      expressiveCode: {
        themes: [codeTheme("filterhelm-dark", "dark"), codeTheme("filterhelm-light", "light")],
        styleOverrides: {
          borderRadius: "6px",
          borderColor: "#496783",
          codeBackground: "#0B1F3B",
          codeForeground: "#E6F4FF",
          codeFontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
          uiFontFamily: "Inter Variable, Inter, system-ui, sans-serif",
          frames: {
            shadowColor: "transparent",
            terminalBackground: "#0B1F3B",
            terminalTitlebarBackground: "#122D4D",
            terminalTitlebarForeground: "#E6F4FF",
          },
        },
      },
      lastUpdated: false,
      credits: false,
    }),
  ],
});
