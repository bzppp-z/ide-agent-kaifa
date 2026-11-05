import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        brand: { DEFAULT: "#0FDC78", hover: "#14EF85", dim: "#0AAE5E" },
        // 面板/表面
        bg: "#0B0D0E",
        surface: { DEFAULT: "#131619", alt: "#191D21", border: "#23282D" },
        panel: "#131619",
        // 文本
        text: { DEFAULT: "#E6E9EC", muted: "#8B949E" },
        muted: { DEFAULT: "#8B949E", dim: "#6E7681" },
      },
      fontFamily: {
        mono: ["JetBrains Mono", "Cascadia Code", "Consolas", "monospace"],
        sans: ["Inter", "PingFang SC", "Microsoft YaHei", "sans-serif"],
      },
      borderRadius: {
        DEFAULT: "8px",
        lg: "12px",
      },
    },
  },
  plugins: [],
};

export default config;
