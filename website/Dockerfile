FROM nginx:alpine-slim

# Install Tailwind CSS standalone CLI, compile CSS, then remove the CLI
COPY . /work
RUN apk add --no-cache curl && \
    curl -fsSL -o /usr/local/bin/tailwindcss \
        https://github.com/tailwindlabs/tailwindcss/releases/download/v3.4.17/tailwindcss-linux-x64 && \
    chmod +x /usr/local/bin/tailwindcss && \
    cd /work && \
    tailwindcss -c tailwind.config.cjs -i styles/input.css -o styles/output.css --minify && \
    rm -f /usr/local/bin/tailwindcss && \
    apk del curl

# Configure nginx
RUN rm -rf /usr/share/nginx/html/* /etc/nginx/conf.d/default.conf && \
    echo 'server { \
        listen 8080; \
        server_tokens off; \
        root /usr/share/nginx/html; \
        index index.html; \
        \
        gzip on; \
        gzip_comp_level 6; \
        gzip_min_length 256; \
        gzip_proxied any; \
        gzip_vary on; \
        gzip_types text/plain text/css application/json application/javascript text/xml application/xml text/javascript image/svg+xml; \
        \
        add_header X-Frame-Options "SAMEORIGIN"; \
        add_header X-Content-Type-Options "nosniff"; \
        add_header Referrer-Policy "strict-origin-when-cross-origin"; \
        add_header Permissions-Policy "camera=(), microphone=(), geolocation=()"; \
        \
        location ~* \.(?:css|js|svg|woff2?)$ { \
            expires 30d; \
            add_header Cache-Control "public, immutable"; \
            add_header X-Content-Type-Options "nosniff"; \
        } \
        \
        location / { \
            try_files $uri $uri/ /index.html; \
            expires 1h; \
            add_header Cache-Control "public, no-transform"; \
        } \
    }' > /etc/nginx/conf.d/optimized.conf && \
    mkdir -p /usr/share/nginx/html/styles /usr/share/nginx/html/scripts \
             /usr/share/nginx/html/assets \
             /var/cache/nginx /var/log/nginx && \
    cp /work/index.html /usr/share/nginx/html/ && \
    cp /work/styles/output.css /usr/share/nginx/html/styles/ && \
    cp /work/scripts/icons.js /usr/share/nginx/html/scripts/ && \
    cp -r /work/assets/. /usr/share/nginx/html/assets/ && \
    rm -rf /work && \
    chown -R nginx:nginx /var/cache/nginx /var/log/nginx /etc/nginx/conf.d /usr/share/nginx/html && \
    touch /var/run/nginx.pid && \
    chown -R nginx:nginx /var/run/nginx.pid

USER nginx

EXPOSE 8080

CMD ["nginx", "-g", "daemon off;"]
