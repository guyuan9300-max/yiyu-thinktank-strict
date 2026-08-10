const typography = require('@tailwindcss/typography');

module.exports = {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['"PingFang SC"', '"SF Pro Display"', '"Helvetica Neue"', 'sans-serif'],
      },
      boxShadow: {
        airy: '0 8px 30px rgba(91,123,254,0.12)',
      },
      colors: {
        airy: {
          blue: '#5B7BFE',
        },
      },
      keyframes: {
        fadeIn: {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
        slideInFromBottom: {
          '0%': { opacity: '0', transform: 'translateY(16px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        zoomIn95: {
          '0%': { opacity: '0', transform: 'scale(0.95)' },
          '100%': { opacity: '1', transform: 'scale(1)' },
        },
      },
      animation: {
        'fade-in': 'fadeIn 240ms ease-out both',
        'slide-in-from-bottom-4': 'slideInFromBottom 280ms ease-out both',
        'zoom-in-95': 'zoomIn95 220ms ease-out both',
      },
    },
  },
  plugins: [typography],
};
