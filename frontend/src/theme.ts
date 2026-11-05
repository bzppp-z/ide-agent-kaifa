/**
 * 品牌 / 主题 tokens（Tailwind 配置与运行时需要的 JS 常量都从这里取值）。
 * 视觉契约：主背景 #0B0D0E，面板 #131619，边框 #23282D，正文 #E6E9EC，
 * 次要文字 #8B949E，品牌绿 #0FDC78（hover #14EF85）。
 */
export const theme = {
  colors: {
    brand: "#0FDC78",
    brandHover: "#14EF85",
    brandDim: "#0AAE5E",
    bg: "#0B0D0E",
    surface: "#131619",
    surfaceAlt: "#191D21",
    border: "#23282D",
    text: "#E6E9EC",
    textDim: "#8B949E",
    terminalBg: "#0B0D0E",
    danger: "#F87171",
  },
  radius: {
    sm: "6px",
    lg: "10px",
  },
  font: {
    sans: 'Inter, "PingFang SC", "Microsoft YaHei", sans-serif',
    mono: '"JetBrains Mono", "Cascadia Code", Consolas, monospace',
  },
} as const;

export type Theme = typeof theme;
