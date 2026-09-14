/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        glyvex: {
          // Dark theme (default)
          bg:        "#000000",
          "bg-2":    "#070707",
          card:      "#0d0d0f",
          "card-hi": "#151518",
          border:    "#1c1c20",
          "border-hi": "#2a2a30",
          text:      "#e8edf5",
          muted:     "#94a3b8",
          "muted-2": "#5b6472",

          // Brand colors
          accent:  "#0d9488",   // teal — primario
          cyan:    "#06b6d4",
          sky:     "#0ea5e9",
          violet:  "#7c3aed",   // AI division
          orange:  "#f97316",
          matrix:  "#00cc33",
        },
      },
      fontFamily: {
        sans: ["'Exo 2'", "system-ui", "sans-serif"],
        mono: ["'JetBrains Mono'", "ui-monospace", "monospace"],
      },
      backgroundImage: {
        "glyvex-gradient": "linear-gradient(135deg, #0d9488, #06b6d4, #0ea5e9)",
        "glyvex-ai-gradient": "linear-gradient(135deg, #7c3aed, #0ea5e9)",
      },
    },
  },
  plugins: [],
};
