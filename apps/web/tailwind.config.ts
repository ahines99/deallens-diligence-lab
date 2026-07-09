import type { Config } from "tailwindcss";

/**
 * DealLens design system — "IC-grade": an editorial, consulting-report aesthetic for
 * private-equity diligence. Deep navy ink, cool paper surfaces, hairline rules, a single
 * confident blue accent + restrained gold, muted editorial status colors, and serif display
 * type over a clean sans. Tailwind's default palette is preserved; these are additive tokens.
 */
const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Ink / text
        ink: {
          DEFAULT: "#0A2A43", // headings, nav
          900: "#071E30",
          800: "#0A2A43",
          700: "#123650",
          600: "#1C4763",
        },
        body: "#22333F", // default body text
        muted: "#5E7284",
        faint: "#94A6B4",

        // Surfaces
        paper: "#F4F6F8", // page canvas
        panel: "#FFFFFF",
        panel2: "#F7F9FB", // subtle fill (table headers, sunken rows)
        sunken: "#EDF1F5",

        // Hairlines / borders
        line: {
          DEFAULT: "#E2E7EC",
          strong: "#CBD4DC",
          faint: "#EDF1F5",
        },

        // Accent (deep professional blue) — used for UI, links, primary actions
        accent: {
          DEFAULT: "#0B4F82",
          hover: "#0A426C",
          soft: "#E9F0F6",
          ring: "#0B4F82",
        },
        // brand kept as an alias so any legacy `brand-*` class maps to the accent ramp
        brand: {
          50: "#E9F0F6",
          100: "#D6E3EF",
          500: "#0B4F82",
          600: "#0A426C",
          700: "#083655",
        },
        gold: {
          DEFAULT: "#B0863C",
          soft: "#F3ECDD",
        },

        // Editorial status / severity (muted, authoritative)
        severity: {
          low: "#2F6E4F",
          medium: "#9A6B1A",
          high: "#B14A2E",
          critical: "#8A2A2A",
        },
        positive: "#2F6E4F",
        negative: "#A23A2E",
        warn: "#9A6B1A",
        info: "#0B4F82",

        // Validated categorical chart series (fixed order — see lib/chartTheme.ts)
        chart: {
          1: "#2E6FA8",
          2: "#1FA089",
          3: "#C98A2C",
          4: "#8A5CB0",
        },
      },
      fontFamily: {
        sans: ["var(--font-sans)", "ui-sans-serif", "system-ui", "Segoe UI", "sans-serif"],
        serif: ["var(--font-serif)", "Georgia", "Cambria", "Times New Roman", "serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },
      fontSize: {
        // Tight, editorial scale
        "2xs": ["0.6875rem", { lineHeight: "1rem", letterSpacing: "0.04em" }],
      },
      borderRadius: {
        sm: "2px",
        DEFAULT: "3px",
        md: "4px",
        lg: "6px",
        xl: "8px",
      },
      boxShadow: {
        // Restrained elevation — hairlines do most of the work
        xs: "0 1px 1px 0 rgba(10,42,67,0.03)",
        sm: "0 1px 2px 0 rgba(10,42,67,0.05), 0 1px 1px 0 rgba(10,42,67,0.03)",
        md: "0 4px 12px -2px rgba(10,42,67,0.08), 0 2px 4px -2px rgba(10,42,67,0.05)",
        panel: "0 1px 2px 0 rgba(10,42,67,0.04)",
      },
      letterSpacing: {
        eyebrow: "0.12em",
      },
      maxWidth: {
        prose: "68ch",
        measure: "78ch",
      },
    },
  },
  plugins: [],
};

export default config;
