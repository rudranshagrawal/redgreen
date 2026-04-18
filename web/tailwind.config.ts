import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "#0B0D0F",
        panel: "#111417",
        line: "#1C2126",
        fg: "#E6E6E6",
        dim: "#8B9196",
        red: "#E04B4B",
        green: "#3FB950",
        amber: "#CF8A4B",
      },
      fontFamily: {
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
    },
  },
  plugins: [],
};

export default config;
