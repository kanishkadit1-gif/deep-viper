/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        viper: {
          bg:     "#0b0e14",
          panel:  "#11151f",
          panel2: "#161b27",
          border: "#222a3a",
          text:   "#e6e9f0",
          muted:  "#8a93a6",
          accent: "#5b8cff",
          good:   "#34d399",
          warn:   "#fbbf24",
          bad:    "#f87171",
        },
      },
      fontFamily: {
        mono: ["JetBrains Mono", "ui-monospace", "SFMono-Regular", "monospace"],
      },
      keyframes: {
        slidein: { "0%": { opacity: 0, transform: "translateY(8px)" },
                   "100%": { opacity: 1, transform: "translateY(0)" } },
        pulseDot: { "0%,100%": { opacity: 1 }, "50%": { opacity: 0.3 } },
      },
      animation: {
        slidein: "slidein 0.25s ease-out",
        pulseDot: "pulseDot 1.2s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};
