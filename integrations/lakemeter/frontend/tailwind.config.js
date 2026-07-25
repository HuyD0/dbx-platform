/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./src/**/*.{ts,tsx}",
    "../../../vendor/lakemeter/frontend/src/**/*.{ts,tsx}",
  ],
  darkMode: "class",
  theme: {
    extend: {
      fontFamily: {
        sans: ["system-ui", "-apple-system", "Segoe UI", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      colors: {
        slate: {
          950: "#0d0d0d",
          925: "#151514",
        },
        lava: {
          300: "#79afea",
          400: "#5b9be4",
          500: "#3987e5",
          600: "#2a78d6",
          700: "#1e62b5",
        },
      },
    },
  },
  plugins: [],
}
