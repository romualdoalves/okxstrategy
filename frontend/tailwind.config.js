/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        surface:  '#0d0f14',
        panel:    '#131722',
        border:   '#1e2130',
        muted:    '#787b86',
        accent:   '#2962ff',
        bull:     '#26a69a',
        bear:     '#ef5350',
      },
      fontFamily: { sans: ['Inter', 'system-ui', 'sans-serif'] },
    },
  },
  plugins: [],
}
