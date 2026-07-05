/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ['./**/*.html', './scripts/**/*.js'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Outfit', 'sans-serif'],
      },
      colors: {
        // Macaw scarlet — the logo's dominant red drives the brand
        brand: {
          50: '#fef3f2',
          100: '#fde3e0',
          200: '#fac4bd',
          400: '#f0685c',
          500: '#e5322b',
          600: '#c81f1a',
          900: '#7a1410',
        },
        // the parrot's wing bands: sunflower yellow + tropical blue
        sun: '#f7b500',
        azure: '#2f6fd0',
      },
      animation: {
        'float': 'float 6s ease-in-out infinite',
        'float-delayed': 'float 6s ease-in-out 3s infinite',
        'fade-enter': 'fadeEnter 1.2s cubic-bezier(0.2, 0.8, 0.2, 1) forwards',
        'slide-in-right': 'slideInRight 1s cubic-bezier(0.16, 1, 0.3, 1) 0.5s forwards',
        'slide-in-right-delayed': 'slideInRight 1s cubic-bezier(0.16, 1, 0.3, 1) 0.8s forwards',
      },
      keyframes: {
        float: {
          '0%, 100%': { transform: 'translateY(0)' },
          '50%': { transform: 'translateY(-10px)' },
        },
        eq: {
          '0%, 100%': { transform: 'scaleY(0.15)' },
          '50%': { transform: 'scaleY(1)' },
        },
        fadeEnter: {
          '0%': { opacity: '0', transform: 'translateY(40px) scale(0.98)', filter: 'blur(10px)' },
          '100%': { opacity: '1', transform: 'translateY(0) scale(1)', filter: 'blur(0)' },
        },
        slideInRight: {
          '0%': { opacity: '0', transform: 'translateX(30px)' },
          '100%': { opacity: '1', transform: 'translateX(0)' },
        }
      }
    }
  }
}
